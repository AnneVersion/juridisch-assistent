"""
Juridisch Assistent — Lokale AI Client (Ollama)
Draait volledig lokaal — geen data verlaat de computer.
Gebruikt voor: anonimisering (NER), documentclassificatie, juridische analyse, uitleg.
"""
import json
import requests

from ai_cloud import SYSTEM_PROMPT_JURIDISCH


class OllamaClient:
    """Client voor de lokale Ollama API."""

    def __init__(self, base_url='http://localhost:11434', model='llama3.1'):
        self.base_url = base_url.rstrip('/')
        self.model = model

    def is_available(self):
        """Check of Ollama draait."""
        try:
            r = requests.get(f'{self.base_url}/api/tags', timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self):
        """Lijst beschikbare modellen."""
        try:
            r = requests.get(f'{self.base_url}/api/tags', timeout=5)
            if r.status_code == 200:
                data = r.json()
                return [m['name'] for m in data.get('models', [])]
        except Exception:
            pass
        return []

    def generate(self, prompt, system=None, temperature=0.3):
        """
        Stuur prompt naar Ollama en krijg volledig antwoord.
        Lage temperature voor consistente anonimisering.
        """
        payload = {
            'model': self.model,
            'prompt': prompt,
            'stream': False,
            'options': {
                'temperature': temperature,
                'num_predict': 4096,
            }
        }
        if system:
            payload['system'] = system

        try:
            r = requests.post(
                f'{self.base_url}/api/generate',
                json=payload,
                timeout=300  # Ollama kan langzaam zijn op CPU
            )
            if r.status_code == 200:
                return r.json().get('response', '')
            raise RuntimeError(f'Ollama status {r.status_code}: {r.text[:200]}')
        except requests.exceptions.Timeout:
            raise RuntimeError('Ollama timeout — het model doet te lang over het antwoord. Probeer het opnieuw (tweede keer is sneller).')
        except requests.exceptions.ConnectionError:
            raise RuntimeError('Kan niet verbinden met Ollama. Is Ollama gestart? (ollama serve)')
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f'Ollama fout: {e}')

    def generate_stream(self, prompt, system=None, temperature=0.3):
        """
        Streaming versie — yields chunks van tekst.
        Handig voor UI feedback tijdens verwerking.
        """
        payload = {
            'model': self.model,
            'prompt': prompt,
            'stream': True,
            'options': {
                'temperature': temperature,
                'num_predict': 4096,
            }
        }
        if system:
            payload['system'] = system

        try:
            r = requests.post(
                f'{self.base_url}/api/generate',
                json=payload,
                stream=True,
                timeout=120
            )
            for line in r.iter_lines():
                if line:
                    data = json.loads(line)
                    chunk = data.get('response', '')
                    if chunk:
                        yield chunk
                    if data.get('done', False):
                        break
        except Exception as e:
            yield f'[Ollama fout: {e}]'

    def chat(self, messages, temperature=0.3):
        """
        Chat-formaat (system + user + assistant messages).
        """
        payload = {
            'model': self.model,
            'messages': messages,
            'stream': False,
            'options': {
                'temperature': temperature,
            }
        }

        try:
            r = requests.post(
                f'{self.base_url}/api/chat',
                json=payload,
                timeout=120
            )
            if r.status_code == 200:
                return r.json().get('message', {}).get('content', '')
        except Exception as e:
            return f'[Ollama fout: {e}]'

        return ''

    # -------------------------------------------------------------------
    # Juridische analyse functies (zelfde interface als CloudAIClient)
    # -------------------------------------------------------------------
    def analyze_procedure(self, anonymized_text):
        """Analyseer welke juridische procedure het beste past."""
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
        return self.generate(prompt, system=SYSTEM_PROMPT_JURIDISCH)

    def check_deadlines(self, anonymized_text, known_dates=None):
        """Identificeer en bereken relevante termijnen."""
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
        return self.generate(prompt, system=SYSTEM_PROMPT_JURIDISCH)

    def generate_draft(self, procedure_type, case_summary, template_instructions=''):
        """Genereer een concept juridisch document."""
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
        return self.generate(prompt, system=SYSTEM_PROMPT_JURIDISCH, temperature=0.4)

    def explain_document(self, anonymized_text):
        """Leg een document uit in begrijpelijke taal."""
        prompt = f"""Leg het volgende GEANONIMISEERDE juridische document uit in eenvoudige, begrijpelijke Nederlandse taal.

Structureer je uitleg als volgt:
1. SAMENVATTING: Wat staat er in dit document? (2-3 zinnen)
2. WAT BETEKENT DIT: Wat zijn de gevolgen voor de betrokken persoon?
3. WAT MOET ER GEBEUREN: Welke acties zijn nodig?
4. TERMIJNEN: Zijn er deadlines waar rekening mee moet worden gehouden?
5. LET OP: Belangrijke waarschuwingen of valkuilen

GEANONIMISEERD DOCUMENT:
{anonymized_text}"""
        return self.generate(prompt, system=SYSTEM_PROMPT_JURIDISCH)

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
        return self.generate(prompt, system=SYSTEM_PROMPT_JURIDISCH)

    def is_configured(self):
        """Check of Ollama beschikbaar is (equivalent van CloudAIClient.is_configured)."""
        return self.is_available()

    def health(self):
        """Uitgebreide health check."""
        result = {
            'available': False,
            'url': self.base_url,
            'model': self.model,
            'models': [],
            'model_loaded': False,
        }

        if self.is_available():
            result['available'] = True
            result['models'] = self.list_models()
            result['model_loaded'] = self.model in result['models'] or \
                any(self.model in m for m in result['models'])

        return result
