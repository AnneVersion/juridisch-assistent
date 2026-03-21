"""
Juridisch Assistent — Meldpunt Ambtenaren Client
API client voor het indienen van meldingen bij Meldpunt Ambtenaren.
Volgt het exacte API-contract van meldpunt-ambtenaren/backend/app.py POST /api/meldingen.
"""
import requests


class MeldpuntClient:
    """Client voor de Meldpunt Ambtenaren API."""

    def __init__(self, base_url='http://localhost:5000/api'):
        self.base_url = base_url.rstrip('/')

    def is_available(self):
        """Check of Meldpunt API bereikbaar is."""
        try:
            r = requests.get(f'{self.base_url}/meldingen', timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def submit_melding(self, data):
        """
        Dien een melding in bij Meldpunt Ambtenaren.

        Args:
            data: dict met:
                - namen: list van {naam: str, functie: str}  (verplicht)
                - titel: str (verplicht)
                - verhaal: str (verplicht)
                - instantie: str
                - feiten: list van int (indices)
                - anoniem: bool
                - melder_naam: str (als niet anoniem)
                - melder_email: str (als niet anoniem)
                - bronnen: list van {t: str, u: str}

        Returns:
            dict: {ok: bool, id: str, claimCode: str, status: str} of {error: str}
        """
        # Minimale validatie
        if not data.get('namen') or not data.get('titel') or not data.get('verhaal'):
            return {'ok': False, 'error': 'Namen, titel en verhaal zijn verplicht'}

        try:
            r = requests.post(
                f'{self.base_url}/meldingen',
                json=data,
                timeout=15,
                headers={'Content-Type': 'application/json'}
            )

            result = r.json()
            if r.status_code in (200, 201):
                result['ok'] = True
                return result
            else:
                return {'ok': False, 'error': result.get('error', f'HTTP {r.status_code}')}

        except requests.ConnectionError:
            return {'ok': False, 'error': 'Kan niet verbinden met Meldpunt Ambtenaren. Draait de backend?'}
        except requests.Timeout:
            return {'ok': False, 'error': 'Timeout bij verbinden met Meldpunt Ambtenaren'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def build_melding_from_zaak(self, zaak, documenten=None):
        """
        Bouw een melding-object op basis van een Zaak.
        Gebruiker kan dit nog aanpassen voor verzending.

        Args:
            zaak: Zaak model instance (of dict)
            documenten: lijst van Document instances (of dicts)

        Returns:
            dict: vooringevuld melding-object
        """
        z = zaak if isinstance(zaak, dict) else zaak.to_dict()

        # Bouw namen-array
        namen = []
        if z.get('wederpartij'):
            namen.append({
                'naam': z['wederpartij'],
                'functie': 'Wederpartij / betrokken ambtenaar',
            })

        # Bouw verhaal uit omschrijving + notities
        verhaal_parts = []
        if z.get('omschrijving'):
            verhaal_parts.append(z['omschrijving'])
        if z.get('notities'):
            verhaal_parts.append(f"\nNotities:\n{z['notities']}")

        # Voeg document-samenvattingen toe (alleen metadata, geen volledige tekst)
        if documenten:
            verhaal_parts.append('\n\nBijbehorende documenten:')
            for doc in documenten:
                d = doc if isinstance(doc, dict) else doc.to_dict()
                meta = d.get('metadata', {})
                line = f"- {d.get('bestandsnaam', 'onbekend')}"
                if meta.get('onderwerp'):
                    line += f" (betreft: {meta['onderwerp']})"
                if meta.get('kenmerk'):
                    line += f" [kenmerk: {', '.join(meta['kenmerk'])}]"
                verhaal_parts.append(line)

        return {
            'namen': namen,
            'titel': f"Melding: {z.get('naam', 'Onbekende zaak')}",
            'verhaal': '\n'.join(verhaal_parts),
            'instantie': z.get('instantie', ''),
            'anoniem': True,  # Default anoniem — veiliger
            'feiten': [],     # Gebruiker selecteert zelf
            'bronnen': [],
        }

    def health(self):
        """Health check."""
        return {
            'available': self.is_available(),
            'url': self.base_url,
        }
