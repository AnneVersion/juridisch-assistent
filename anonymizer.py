"""
Juridisch Assistent — Anonimisering Pipeline
Drie-pass systeem:
  Pass 1: Regex-gebaseerd (deterministisch) — BSN, telefoon, email, postcode, IBAN
  Pass 2: Contextpatronen (deterministisch) — namen, adressen uit juridische formulieren
  Pass 3: Ollama NER (optioneel) — alles wat de patronen missen
Mapping wordt bewaard zodat de gebruiker kan reviewen en aanpassen.
Gevonden PII wordt opgeslagen als herbruikbare AnonymisatieRegel in de database.
"""
import re
import json
from collections import OrderedDict


# ---------------------------------------------------------------------------
# Regex patronen voor Nederlandse persoonsgegevens
# ---------------------------------------------------------------------------
REGEX_PATTERNS = OrderedDict([
    ('bsn',      (r'\b(\d{9})\b',                                      '[BSN]')),
    ('iban',     (r'\b(NL\d{2}[A-Z]{4}\d{10})\b',                     '[IBAN]')),
    ('telefoon', (r'\b((?:\+31|0)\s*(?:\d[\s\-]*){9})\b',             '[telefoon]')),
    ('email',    (r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b', '[email]')),
    ('postcode', (r'\b(\d{4}\s?[A-Z]{2})\b',                          '[postcode]')),
])

# ---------------------------------------------------------------------------
# Patronen voor namen in juridische documenten
# ---------------------------------------------------------------------------
# Velden die gevolgd worden door een naam
NAAM_CONTEXT_KEYWORDS = [
    # "Naam ..." wordt apart afgehandeld in context_pass (formulier-kolommen)
    r'(?:^|\n)\s*Eiser\s*:?\s*',
    r'(?:^|\n)\s*Gedaagde\s*:?\s*',
    r'(?:^|\n)\s*Verzoeker\s*:?\s*',
    r'(?:^|\n)\s*Verweerder\s*:?\s*',
    r'(?:^|\n)\s*Gemachtigde\s*:?\s*',
    r'(?:^|\n)\s*Melder\s*:?\s*',
    r'(?:^|\n)\s*Klager\s*:?\s*',
    r'(?:^|\n)\s*Appellant\s*:?\s*',
    r'(?:^|\n)\s*(?:mr\.|Mr\.|dhr\.|mevr\.|mw\.)\s+',   # "mr. Jansen"
    r'(?:van|door|namens|aan|voor|tegen)\s+(?:de\s+heer|mevrouw|dhr\.|mevr\.|mw\.)\s+',
    r'(?:de\s+heer|mevrouw|dhr\.|mevr\.|mw\.)\s+',
]

# Patronen voor straatnamen + huisnummer
ADRES_PATTERNS = [
    # "Straatnaam 12" of "Straatnaam 12a" of "Straatnaam 12-14"
    r'\b([A-Z][a-z]+(?:straat|weg|laan|plein|singel|gracht|kade|dijk|dreef|hof|pad|steeg|ring|park|markt|brug|dam|poort|vest|wal|allee)\s+\d+[a-zA-Z]?(?:\s*[-/]\s*\d+[a-zA-Z]?)?)\b',
    # Generiek: "Hoofdletter-woord + nummer" als het op een adresregel staat
    r'(?:^|\n)\s*Adres\s+([A-Z][a-zéëïöü]+(?:\s+[a-z]{1,4})?\s+\d+[a-zA-Z]?(?:\s*[-/]\s*\d+)?)',
    # "Postbus 1234"
    r'\b(Postbus\s+\d+)\b',
]

# Woorden die NOOIT als naam gezien mogen worden
SKIP_WORDS = {
    'naam', 'adres', 'telefoon', 'faxnummer', 'kenmerk', 'zaaksoort',
    'eisende', 'partij', 'gedaagde', 'gemachtigde', 'verhinderdata',
    'bijzondere', 'verzoeken', 'verkorting', 'dagvaardingstermijn',
    'verlenging', 'zittingsduur', 'bijlagen', 'onderwerp', 'datum',
    'rechtbank', 'gerechtshof', 'hoge raad', 'kantonrechter',
    'administratie', 'aanvraag', 'kort', 'geding', 'locatie',
    'zaaksoort', 'kantonzaak', 'enkelvoud', 'meervoud', 'concept',
    'bezwaarschrift', 'beroepschrift', 'dagvaarding', 'rekening',
    'factuur', 'offerte', 'rapport', 'beschikking', 'vonnis',
    'uitspraak', 'kennisgeving', 'sommatie', 'ingebrekestelling',
    'nederland', 'duitsland', 'belgie', 'europa',
    'januari', 'februari', 'maart', 'april', 'mei', 'juni',
    'juli', 'augustus', 'september', 'oktober', 'november', 'december',
    'maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag',
    'nvt', 'nee', 'ja', 'beiden', 'beide', 'weken', 'partijen',
}

# Bekende plaatsnamen — niet anonimiseren als losse plaatsnamen maar wel in adressen
BEKENDE_PLAATSEN = {
    'amsterdam', 'rotterdam', 'den haag', "'s-gravenhage", 'utrecht', 'eindhoven',
    'tilburg', 'groningen', 'almere', 'breda', 'nijmegen', 'enschede', 'haarlem',
    'arnhem', 'zaanstad', 'amersfoort', 'apeldoorn', 'den bosch', "'s-hertogenbosch",
    'hoofddorp', 'maastricht', 'leiden', 'dordrecht', 'zoetermeer', 'zwolle',
    'deventer', 'delft', 'alkmaar', 'heerlen', 'venlo', 'leeuwarden',
    'hilversum', 'emmerich', 'kleef', 'kleve', 'duisburg', 'wesel',
}


def _is_plausible_name(text):
    """Check of een tekst een plausibele persoonsnaam is."""
    text = text.strip()
    if len(text) < 3 or len(text) > 60:
        return False

    # Moet minstens één hoofdletter bevatten
    if not any(c.isupper() for c in text):
        return False

    words = text.split()
    if len(words) < 1 or len(words) > 5:
        return False

    # Eerste woord moet met hoofdletter beginnen (tenzij tussenvoegsel)
    tussenvoegsels = {'de', 'van', 'den', 'der', 'het', 'ter', 'ten', 'te', 'op', 'in', "'t", 'la', 'le', 'du'}
    significant_words = [w for w in words if w.lower() not in tussenvoegsels]

    if not significant_words:
        return False

    # Ten minste één "naamwoord" moet met hoofdletter beginnen
    if not any(w[0].isupper() for w in significant_words):
        return False

    # Mag niet in skip-lijst staan
    if text.lower() in SKIP_WORDS:
        return False

    # Geen woord mag een skip-woord zijn
    for w in words:
        if w.lower() in SKIP_WORDS:
            return False

    # Mag geen getal bevatten (dan is het een kenmerk of iets anders)
    if any(c.isdigit() for c in text):
        return False

    return True


def _extract_name_after(text, pos, max_words=4):
    """Extraheer een naam na een bepaalde positie in de tekst."""
    rest = text[pos:pos+100]  # Max 100 tekens vooruit kijken

    # Neem woorden tot we een niet-naam woord tegenkomen
    words = []
    tussenvoegsels = {'de', 'van', 'den', 'der', 'het', 'ter', 'ten', 'te', 'op', 'in', "'t"}

    for word in rest.split():
        # Stop bij leestekens, nummers, of newlines
        if not word or word in ('\n', '\r'):
            break

        clean = word.strip('.,;:!?()')
        if not clean:
            break

        # Tussenvoegsel: altijd doorgaan
        if clean.lower() in tussenvoegsels:
            words.append(clean)
            continue

        # Hoofdletter: waarschijnlijk deel van de naam
        if clean[0].isupper() and clean.lower() not in SKIP_WORDS:
            words.append(clean)
            if len(words) >= max_words:
                break
            continue

        # Alles anders: stoppen
        break

    if not words:
        return None

    name = ' '.join(words)
    # Strip trailing tussenvoegsels
    while words and words[-1].lower() in tussenvoegsels:
        words.pop()
        name = ' '.join(words)

    if _is_plausible_name(name):
        return name
    return None


class Anonymizer:
    """Anonimisering pipeline voor Nederlandse juridische documenten."""

    def __init__(self, ollama_client=None):
        self.ollama = ollama_client
        self._counter = {}
        self._found_pii = []  # Lijst van (origineel, type) tuples

    def _next_label(self, type_name):
        """Genereer volgnummer-label: 'Persoon 1', 'Persoon 2', etc."""
        self._counter[type_name] = self._counter.get(type_name, 0) + 1
        return f'{type_name} {self._counter[type_name]}'

    @property
    def found_pii(self):
        """Alle gevonden PII items: [(origineel, type, vervanging), ...]"""
        return list(self._found_pii)

    # -------------------------------------------------------------------
    # Pass 1: Regex (BSN, IBAN, telefoon, email, postcode)
    # -------------------------------------------------------------------
    def regex_pass(self, text, existing_map=None):
        """
        Vervang alle regex-matches door anonieme labels.
        Returns: (geanonimiseerde_tekst, mapping_dict)
        """
        mapping = dict(existing_map) if existing_map else {}
        result = text

        for ptype, (pattern, label_template) in REGEX_PATTERNS.items():
            for match in re.finditer(pattern, text):
                original = match.group(1).strip()
                if not original or original in mapping:
                    continue

                # Gebruik vaste label of genummerd label
                existing_of_type = [v for v in mapping.values()
                                    if v.startswith(label_template.rstrip(']'))]
                if existing_of_type:
                    label = f'{label_template.rstrip("]")} {len(existing_of_type) + 1}]'
                else:
                    label = label_template

                mapping[original] = label
                self._found_pii.append((original, ptype, label))

        # Vervang in tekst (langste match eerst om overlap te voorkomen)
        for original, replacement in sorted(mapping.items(), key=lambda x: -len(x[0])):
            result = result.replace(original, replacement)

        return result, mapping

    # -------------------------------------------------------------------
    # Pass 2: Contextpatronen (namen, adressen uit juridische documenten)
    # -------------------------------------------------------------------
    def context_pass(self, text, existing_map=None):
        """
        Detecteer namen en adressen op basis van contextpatronen in
        Nederlandse juridische documenten. Werkt ZONDER Ollama.
        Returns: (geanonimiseerde_tekst, bijgewerkte_mapping)
        """
        mapping = dict(existing_map) if existing_map else {}
        already_mapped = set(mapping.keys())

        # --- Naam-detectie via contextwoorden ---
        for kw_pattern in NAAM_CONTEXT_KEYWORDS:
            for m in re.finditer(kw_pattern, text, re.IGNORECASE):
                name = _extract_name_after(text, m.end())
                if name and name not in already_mapped:
                    label = self._next_label('Persoon')
                    mapping[name] = label
                    already_mapped.add(name)
                    self._found_pii.append((name, 'naam', label))

        # --- Naam-detectie: "Naam X Y Z" regel uit formulieren ---
        # Juridische formulieren hebben vaak "Naam [voornaam achternaam]" of
        # twee namen naast elkaar (eisende partij / gedaagde partij)
        for m in re.finditer(r'(?:^|\n)\s*Naam\s+(.+?)(?=\n|$)', text, re.MULTILINE):
            raw_names = m.group(1).strip()
            # Split op dubbele spaties (kolom-scheiding in formulieren)
            columns = re.split(r'\s{2,}', raw_names)

            for col in columns:
                col = col.strip()
                if not col:
                    continue

                # Probeer de kolom als naam te herkennen
                parts = col.split()
                current_name_parts = []
                names_found = []
                tussenvoegsels = {'de', 'van', 'den', 'der', 'het', 'ter', 'ten', 'te'}

                # Verzamel alle naam-woorden in volgorde
                all_name_words = []
                for part in parts:
                    clean = part.strip('.,;:')
                    if not clean:
                        continue
                    if clean[0].isupper() and clean.lower() not in SKIP_WORDS:
                        all_name_words.append(clean)
                    elif clean.lower() in tussenvoegsels:
                        all_name_words.append(clean)
                    else:
                        break  # Stop bij niet-naam woorden

                # Heuristiek: als er precies 2 woorden zijn, is het 1 naam
                # Als er 3+ woorden zijn, probeer te splitsen in voor+achternaam combinaties
                if len(all_name_words) <= 3:
                    candidate = ' '.join(all_name_words)
                    if _is_plausible_name(candidate):
                        names_found.append(candidate)
                elif len(all_name_words) >= 4:
                    # Waarschijnlijk twee namen naast elkaar
                    # Probeer de beste split te vinden (bijv. 2+2, 2+3, 3+2)
                    best_split = None
                    for split_at in range(2, len(all_name_words) - 1):
                        name_a = ' '.join(all_name_words[:split_at])
                        name_b = ' '.join(all_name_words[split_at:])
                        if _is_plausible_name(name_a) and _is_plausible_name(name_b):
                            best_split = (name_a, name_b)
                            break
                    if best_split:
                        names_found.extend(best_split)
                    else:
                        # Kon niet splitsen, neem alles als 1 naam
                        candidate = ' '.join(all_name_words)
                        if _is_plausible_name(candidate):
                            names_found.append(candidate)

                for name in names_found:
                    if name not in already_mapped:
                        label = self._next_label('Persoon')
                        mapping[name] = label
                        already_mapped.add(name)
                        self._found_pii.append((name, 'naam', label))

        # --- Adres-detectie ---
        for adres_pat in ADRES_PATTERNS:
            for m in re.finditer(adres_pat, text, re.IGNORECASE):
                addr = m.group(1).strip() if m.lastindex else m.group(0).strip()
                if addr and addr not in already_mapped and len(addr) > 5:
                    label = self._next_label('[adres')
                    label = label + ']'
                    mapping[addr] = label
                    already_mapped.add(addr)
                    self._found_pii.append((addr, 'adres', label))

        # --- Adres-detectie via contextwoord ---
        for m in re.finditer(r'(?:^|\n)\s*Adres\s+(.+?)(?=\n|$)', text, re.MULTILINE):
            addr_text = m.group(1).strip()
            # Kan meerdere adressen bevatten (eisende + gedaagde naast elkaar)
            # Split op dubbele spaties
            parts = re.split(r'\s{2,}', addr_text)
            if len(parts) == 1:
                # Geen dubbele spatie — probeer te splitsen op straatnaam-patronen
                # Bijv. "Laarfeldweg 2 Gubbelstraat 10C01"
                addr_splits = re.findall(
                    r'[A-Z][a-zéëïöü]+(?:\s+[a-z]{1,4})?\s+\d+[a-zA-Z0-9]*',
                    parts[0])
                if len(addr_splits) >= 2:
                    parts = addr_splits
            for part in parts:
                part = part.strip()
                if part and part not in already_mapped and len(part) > 5:
                    if re.search(r'[A-Za-z]+\s+\d', part):
                        label = self._next_label('[adres')
                        label = label + ']'
                        mapping[part] = label
                        already_mapped.add(part)
                        self._found_pii.append((part, 'adres', label))

        # --- Plaats-detectie na postcode ---
        for m in re.finditer(r'\b\d{4}\s?[A-Z]{2}\s+([A-Z][a-zéëïöüà]+(?:\s+[A-Z][a-z]+)*)', text):
            plaats = m.group(1).strip()
            if plaats and plaats not in already_mapped and plaats.lower() in BEKENDE_PLAATSEN:
                label = '[plaats]'
                existing_plaatsen = [v for v in mapping.values() if v.startswith('[plaats')]
                if existing_plaatsen:
                    label = f'[plaats {len(existing_plaatsen) + 1}]'
                mapping[plaats] = label
                already_mapped.add(plaats)
                self._found_pii.append((plaats, 'plaats', label))

        # Vervang in tekst (langste match eerst)
        result = text
        for original, replacement in sorted(mapping.items(), key=lambda x: -len(x[0])):
            result = result.replace(original, replacement)

        return result, mapping

    # -------------------------------------------------------------------
    # Pass 3: Ollama NER (optioneel)
    # -------------------------------------------------------------------
    def ollama_pass(self, text, existing_map=None):
        """
        Stuur tekst naar lokale Ollama voor NER (Named Entity Recognition).
        Identificeert namen, organisaties, adressen die regex/context missen.
        Returns: (geanonimiseerde_tekst, bijgewerkte_mapping)
        """
        if not self.ollama:
            return text, existing_map or {}

        mapping = dict(existing_map) if existing_map else {}

        prompt = f"""Je bent een anonimisatie-specialist voor Nederlandse juridische documenten.

Analyseer de volgende tekst en identificeer ALLE persoonsgegevens die nog niet zijn geanonimiseerd:
- Persoonsnamen (voor- en achternamen) → vervang door "Persoon 1", "Persoon 2", etc.
- Organisatienamen die een persoon kunnen identificeren → vervang door "Instantie 1", etc.
- Straatnamen en huisnummers → vervang door "[adres]"
- Plaatsnamen → vervang door "[plaats]"

BEWAAR ongewijzigd:
- Juridische termen en wetsartikelen
- Procedure-namen (bezwaar, beroep, etc.)
- Overheidsinstellingen die algemeen bekend zijn (Belastingdienst, UWV, gemeente, etc.)
- Datums (al vervangen door regex)
- Tekst die al tussen [ ] staat (al geanonimiseerd)

Retourneer ALLEEN een JSON object met de mapping van originele tekst naar vervanging.
Voorbeeld: {{"Jan de Vries": "Persoon 1", "Kalverstraat 12": "[adres]"}}

Als er niets te anonimiseren valt, retourneer: {{}}

TEKST:
{text}"""

        try:
            response = self.ollama.generate(prompt)
            # Probeer JSON te parseren uit de response
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                new_mappings = json.loads(json_match.group())
                for original, replacement in new_mappings.items():
                    original = original.strip()
                    if original and original not in mapping and len(original) > 1:
                        mapping[original] = replacement
                        # Bepaal type
                        ptype = 'overig'
                        if replacement.startswith('Persoon'):
                            ptype = 'naam'
                        elif replacement.startswith('[adres'):
                            ptype = 'adres'
                        elif replacement.startswith('[plaats'):
                            ptype = 'plaats'
                        elif replacement.startswith('Instantie'):
                            ptype = 'organisatie'
                        self._found_pii.append((original, ptype, replacement))
        except (json.JSONDecodeError, Exception):
            pass

        # Vervang in tekst
        result = text
        for original, replacement in sorted(mapping.items(), key=lambda x: -len(x[0])):
            result = result.replace(original, replacement)

        return result, mapping

    # -------------------------------------------------------------------
    # Volledige pipeline
    # -------------------------------------------------------------------
    def anonymize(self, text, existing_map=None):
        """
        Volledige anonimisering: regex → context → Ollama NER.
        Returns: (geanonimiseerde_tekst, mapping_dict)
        """
        self._counter = {}
        self._found_pii = []

        # Pass 1: Regex (BSN, IBAN, telefoon, email, postcode)
        _, mapping = self.regex_pass(text, existing_map)

        # Pass 2: Contextpatronen (namen, adressen)
        _, mapping = self.context_pass(text, mapping)

        # Pass 3: Ollama NER (als beschikbaar)
        if self.ollama and self.ollama.is_available():
            _, mapping = self.ollama_pass(text, mapping)

        # Finale vervanging op de originele tekst
        result = text
        for original, replacement in sorted(mapping.items(), key=lambda x: -len(x[0])):
            result = result.replace(original, replacement)

        return result, mapping

    def regex_only(self, text, existing_map=None):
        """Regex + context patronen — voor als Ollama niet beschikbaar is."""
        self._counter = {}
        self._found_pii = []

        # Pass 1: Regex
        _, mapping = self.regex_pass(text, existing_map)

        # Pass 2: Context patronen (namen, adressen)
        _, mapping = self.context_pass(text, mapping)

        # Finale vervanging
        result = text
        for original, replacement in sorted(mapping.items(), key=lambda x: -len(x[0])):
            result = result.replace(original, replacement)

        return result, mapping

    # -------------------------------------------------------------------
    # De-anonimisering (voor gegenereerde documenten)
    # -------------------------------------------------------------------
    @staticmethod
    def de_anonymize(anonymized_text, mapping):
        """
        Herstel originele tekst van geanonimiseerde versie.
        Gebruikt omgekeerde mapping.
        """
        result = anonymized_text
        reverse = {v: k for k, v in mapping.items()}
        for replacement, original in sorted(reverse.items(), key=lambda x: -len(x[0])):
            result = result.replace(replacement, original)
        return result

    # -------------------------------------------------------------------
    # Mapping validatie / statistieken
    # -------------------------------------------------------------------
    @staticmethod
    def mapping_stats(mapping):
        """Bereken statistieken over de anonimisering."""
        type_counts = {}
        for original, replacement in mapping.items():
            if replacement.startswith('[BSN'):
                t = 'bsn'
            elif replacement.startswith('[IBAN'):
                t = 'iban'
            elif replacement.startswith('[telefoon'):
                t = 'telefoon'
            elif replacement.startswith('[email'):
                t = 'email'
            elif replacement.startswith('[postcode'):
                t = 'postcode'
            elif replacement.startswith('[datum'):
                t = 'datum'
            elif replacement.startswith('[adres'):
                t = 'adres'
            elif replacement.startswith('[plaats'):
                t = 'plaats'
            elif replacement.startswith('Persoon'):
                t = 'persoon'
            elif replacement.startswith('Instantie'):
                t = 'instantie'
            else:
                t = 'overig'
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            'total': len(mapping),
            'types': type_counts,
        }
