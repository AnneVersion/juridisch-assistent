# Juridisch Assistent

Lokale applicatie voor het beheren van juridische zaken, documenten anonimiseren en juridisch onderzoek. **Alle data blijft op je eigen computer** — er wordt niets naar het internet gestuurd.

## Wat kan het?

- **Zaken beheren** — rechtszaken, bezwaarschriften, beroepsprocedures bijhouden
- **Documenten anonimiseren** — BSN-nummers, namen, adressen en andere persoonsgegevens automatisch detecteren en vervangen
- **Rechtspraak.nl doorzoeken** — jurisprudentie zoeken en koppelen aan je zaak
- **AI-analyse** — documenten analyseren met lokale AI (Ollama) of cloud AI
- **Documenten scannen** — mappen scannen op relevante juridische documenten
- **Email** — correspondentie bijhouden per zaak
- **Meldpunt koppeling** — integratie met Meldpunt Ambtenaren app

## Installatie

```bash
# Clone de repository
git clone https://github.com/AnneVersion/juridisch-assistent.git
cd juridisch-assistent

# Installeer dependencies
pip install -r requirements.txt

# Start de applicatie
python app.py
```

Open daarna `http://localhost:5001` in je browser.

## Privacy & Veiligheid

- **Alle data blijft lokaal** — SQLite database op je eigen computer
- **Geen cloud uploads** — documenten worden niet naar internet gestuurd
- **AI is optioneel** — werkt ook zonder AI (Ollama draait lokaal, cloud AI is opt-in)
- **Anonimisering** — verwijdert automatisch persoonsgegevens uit documenten voordat je ze deelt

## Vereisten

- Python 3.10+
- Flask
- SQLAlchemy
- (Optioneel) Ollama voor lokale AI-analyse

## Bestanden

| Bestand | Functie |
|---------|---------|
| `app.py` | Flask server (port 5001) |
| `models.py` | Database modellen (Zaak, Document, Analyse, etc.) |
| `anonymizer.py` | PII detectie en anonimisering |
| `document_processor.py` | Document parsing en metadata extractie |
| `rechtspraak.py` | Rechtspraak.nl API integratie |
| `ai_local.py` | Ollama (lokale AI) client |
| `ai_cloud.py` | Cloud AI client (optioneel) |
| `email_client.py` | Email functionaliteit |
| `config.py` | Configuratie |

## Licentie

MIT — vrij te gebruiken en aan te passen.

---

Gebouwd met Claude
