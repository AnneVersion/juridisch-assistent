"""
Juridisch Assistent — Cloud AI Client (OpenAI / Anthropic)
Ontvangt ALLEEN geanonimiseerde tekst. Nooit persoonsgegevens.
Gebruikt voor: juridische analyse, procedure-advies, document generatie.
"""


# ---------------------------------------------------------------------------
# Juridische System Prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_JURIDISCH = """Je bent een Nederlandse juridische assistent. Je analyseert GEANONIMISEERDE documenten.
Je kent het Nederlandse rechtssysteem grondig:

BESTUURSRECHT:
- Klacht (hoofdstuk 9 Awb): tegen gedrag/handelen van ambtenaren. Geen wettelijke termijn voor indienen, bestuursorgaan moet binnen 6 weken afhandelen.
- Bezwaar (art. 7:1 Awb): tegen een beschikking (besluit). Termijn: 6 weken na bekendmaking/verzending. Pro forma bezwaar mogelijk bij dreigende termijnoverschrijding.
- Beroep (art. 8:1 Awb): bij de bestuursrechter, na afwijzing bezwaar. Termijn: 6 weken na bekendmaking bezwaarbesluit.
- Hoger beroep: bij Afdeling bestuursrechtspraak Raad van State (ABRvS) of Centrale Raad van Beroep (CRvB). Termijn: 6 weken na uitspraak.
- Voorlopige voorziening (art. 8:81 Awb): spoedprocedure bij de voorzieningenrechter.
- WOO-verzoek (Wet open overheid, opvolger Wob): informatieverzoek aan bestuursorgaan. Beslistermijn: 4 weken, verlengbaar met 2 weken.

CIVIEL RECHT:
- Dagvaarding (art. 45 Rv): via deurwaarder, diverse termijnen afhankelijk van zaaksoort.
- Verzoekschrift (art. 261 Rv): familie-, jeugd-, insolventierecht, arbeidsrecht.
- Kort geding: spoedprocedure bij de voorzieningenrechter.
- Kantonprocedure: vorderingen tot €25.000, arbeids- en huurzaken.

STRAFRECHT:
- Aangifte (art. 161 Sv): bij politie.
- Art. 12 Sv-procedure: klacht bij het gerechtshof tegen beslissing OM om niet te vervolgen. Termijn: 3 maanden na kennisgeving sepot.

TOESLAGENAFFAIRE / OVERHEIDSHANDELEN:
- Schadevergoeding via bestuursrechter (art. 8:88 Awb) of civiele rechter.
- Nationale ombudsman: klacht over overheidshandelen.
- Kinderopvangtoeslagenaffaire herstelregeling (UHT).

BELANGRIJKE REGELS:
- Je bent GEEN advocaat. Adviseer altijd om een jurist te raadplegen voor complexe zaken.
- Noem altijd de wettelijke basis (artikelnummers).
- Waarschuw expliciet als een termijn dreigt te verlopen.
- Geef bij elk advies aan hoe zeker je bent (hoog/gemiddeld/laag).
- Gebruik alleen de geanonimiseerde tekst — vraag NOOIT om persoonsgegevens."""


class CloudAIClient:
    """Client voor cloud AI (OpenAI of Anthropic) — alleen geanonimiseerde tekst."""

    def __init__(self, provider='openai', api_key='', model='gpt-4o'):
        self.provider = provider
        self.api_key = api_key
        self.model = model

    def is_configured(self):
        """Check of API key is ingesteld."""
        return bool(self.api_key)

    def _call_openai(self, system, user_msg, temperature=0.3):
        """OpenAI API call."""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                messages=[
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': user_msg},
                ],
                max_tokens=4096,
            )
            return response.choices[0].message.content
        except ImportError:
            return '[Fout: openai package niet geinstalleerd. pip install openai]'
        except Exception as e:
            return f'[OpenAI fout: {e}]'

    def _call_anthropic(self, system, user_msg, temperature=0.3):
        """Anthropic API call."""
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=temperature,
                system=system,
                messages=[
                    {'role': 'user', 'content': user_msg},
                ],
            )
            return response.content[0].text
        except ImportError:
            return '[Fout: anthropic package niet geinstalleerd. pip install anthropic]'
        except Exception as e:
            return f'[Anthropic fout: {e}]'

    def _call(self, system, user_msg, temperature=0.3):
        """Dispatch naar juiste provider."""
        if self.provider == 'anthropic':
            return self._call_anthropic(system, user_msg, temperature)
        return self._call_openai(system, user_msg, temperature)

    # -------------------------------------------------------------------
    # Juridische analyse functies
    # -------------------------------------------------------------------
    def analyze_procedure(self, anonymized_text):
        """
        Analyseer welke juridische procedure het beste past.
        Returns: gestructureerd advies als tekst.
        """
        prompt = f"""Analyseer het volgende GEANONIMISEERDE document en adviseer welke juridische procedure het meest geschikt is.

Geef je antwoord in het volgende formaat:
1. DOCUMENTTYPE: Wat voor soort document is dit? (beschikking, brief, vonnis, etc.)
2. AFZENDER: Welk type organisatie heeft dit gestuurd? (bestuursorgaan, rechtbank, etc.)
3. AANBEVOLEN PROCEDURE: Welke procedure is het meest geschikt?
4. WETTELIJKE BASIS: Relevante wetsartikelen
5. TERMIJN: Wat is de termijn en wanneer verloopt die (indien te bepalen)?
6. STAPPEN: Welke concrete stappen moet de persoon nemen?
7. ZEKERHEID: Hoe zeker ben je van dit advies? (hoog/gemiddeld/laag)
8. WAARSCHUWINGEN: Belangrijke aandachtspunten

GEANONIMISEERD DOCUMENT:
{anonymized_text}"""

        return self._call(SYSTEM_PROMPT_JURIDISCH, prompt)

    def check_deadlines(self, anonymized_text, known_dates=None):
        """
        Identificeer en bereken relevante termijnen.
        """
        dates_info = ''
        if known_dates:
            dates_info = f'\n\nBekende datums uit het document: {", ".join(known_dates)}'

        prompt = f"""Analyseer het volgende GEANONIMISEERDE document en identificeer alle relevante juridische termijnen.

Voor elke termijn, geef:
1. TYPE: Wat voor termijn (bezwaartermijn, beroepstermijn, etc.)
2. WETTELIJKE BASIS: Welk wetsartikel
3. DUUR: Hoe lang is de termijn (bijv. 6 weken)
4. STARTDATUM: Wanneer begint de termijn (indien te bepalen)
5. EINDDATUM: Wanneer verloopt de termijn (indien te berekenen)
6. STATUS: Dreigt de termijn te verlopen? Is actie urgent?{dates_info}

GEANONIMISEERD DOCUMENT:
{anonymized_text}"""

        return self._call(SYSTEM_PROMPT_JURIDISCH, prompt)

    def generate_draft(self, procedure_type, case_summary, template_instructions=''):
        """
        Genereer een concept juridisch document.
        """
        prompt = f"""Genereer een concept {procedure_type} op basis van de volgende zaakgegevens.

ZAAKGEGEVENS (geanonimiseerd):
{case_summary}

{f"TEMPLATE INSTRUCTIES: {template_instructions}" if template_instructions else ""}

EISEN AAN HET DOCUMENT:
- Formele Nederlandse juridische taal
- Correcte structuur voor een {procedure_type}
- Verwijs naar relevante wetsartikelen
- Gebruik de geanonimiseerde namen (Persoon 1, Instantie 1, etc.)
- Voeg [INVULLEN] toe waar specifieke informatie ontbreekt
- Eindig met een verzoek/petitum

Genereer het volledige concept document:"""

        return self._call(SYSTEM_PROMPT_JURIDISCH, prompt, temperature=0.4)

    def explain_document(self, anonymized_text):
        """
        Leg een document uit in begrijpelijke taal.
        """
        prompt = f"""Leg het volgende GEANONIMISEERDE juridische document uit in eenvoudige, begrijpelijke Nederlandse taal.

Structureer je uitleg als volgt:
1. SAMENVATTING: Wat staat er in dit document? (2-3 zinnen)
2. WAT BETEKENT DIT: Wat zijn de gevolgen voor de betrokken persoon?
3. WAT MOET ER GEBEUREN: Welke acties zijn nodig?
4. TERMIJNEN: Zijn er deadlines waar rekening mee moet worden gehouden?
5. LET OP: Belangrijke waarschuwingen of valkuilen

GEANONIMISEERD DOCUMENT:
{anonymized_text}"""

        return self._call(SYSTEM_PROMPT_JURIDISCH, prompt)

    def generate_summary(self, anonymized_text):
        """Genereer een samenvatting van alle documenten in een zaak."""
        prompt = f"""Maak een beknopte SAMENVATTING van de volgende zaak op basis van alle GEANONIMISEERDE documenten.

Structureer je samenvatting als volgt:
1. KERN: Waar gaat deze zaak over? (2-3 zinnen)
2. PARTIJEN: Wie zijn de betrokken partijen? (gebruik alleen geanonimiseerde namen)
3. CHRONOLOGIE: Wat is er gebeurd, in chronologische volgorde? (korte bullets)
4. HUIDIGE STATUS: Wat is de huidige stand van zaken?
5. KERNVRAGEN: Welke juridische vragen spelen er?

Schrijf in helder, zakelijk Nederlands. Gebruik alleen informatie uit de documenten.

GEANONIMISEERDE DOCUMENTEN:
{anonymized_text}"""
        return self._call(SYSTEM_PROMPT_JURIDISCH, prompt)

    def health(self):
        """Health check voor cloud AI."""
        return {
            'configured': self.is_configured(),
            'provider': self.provider,
            'model': self.model,
            'has_key': bool(self.api_key),
        }
