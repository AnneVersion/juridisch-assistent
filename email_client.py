"""
Juridisch Assistent — Microsoft Graph E-mail Client
Koppelt met Outlook/Hotmail via OAuth2. Alle tokens lokaal opgeslagen.
"""
import json
import time
import requests as http_requests
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Graph API constanten
# ---------------------------------------------------------------------------
GRAPH_BASE = 'https://graph.microsoft.com/v1.0'
AUTHORITY = 'https://login.microsoftonline.com/consumers'  # Personal MS accounts
SCOPES = ['Mail.Read', 'Mail.ReadBasic', 'User.Read']

# Azure AD app registration voor lokale tools (public client)
# De gebruiker kan ook zijn eigen app-id instellen via Instellingen
DEFAULT_CLIENT_ID = ''  # Moet door gebruiker worden ingevuld


class OutlookClient:
    """Microsoft Graph API client voor e-mail zoeken."""

    def __init__(self, client_id='', tokens=None):
        self.client_id = client_id or DEFAULT_CLIENT_ID
        self.tokens = tokens or {}  # {access_token, refresh_token, expires_at}
        self._msal_app = None

    # ------------------------------------------------------------------
    # MSAL setup
    # ------------------------------------------------------------------
    def _get_msal(self):
        """Lazy-load MSAL PublicClientApplication."""
        if self._msal_app is None:
            try:
                import msal
                self._msal_app = msal.PublicClientApplication(
                    self.client_id,
                    authority=AUTHORITY,
                )
            except ImportError:
                raise RuntimeError('msal niet geïnstalleerd. Run: pip install msal')
        return self._msal_app

    # ------------------------------------------------------------------
    # Device Code Flow (gebruiksvriendelijkst voor lokale tool)
    # ------------------------------------------------------------------
    def start_device_flow(self):
        """Start device code flow. Geeft instructies terug voor de gebruiker.

        Returns: {
            'user_code': 'ABC123',
            'verification_uri': 'https://microsoft.com/devicelogin',
            'message': 'Ga naar ... en voer code ... in',
            'expires_in': 900,
            'flow': <flow object voor acquire_token>
        }
        """
        if not self.client_id:
            return {'error': 'Geen Microsoft Client ID ingesteld. Maak een app-registratie aan in Azure Portal.'}

        app = self._get_msal()
        flow = app.initiate_device_flow(scopes=SCOPES)

        if 'error' in flow:
            return {'error': flow.get('error_description', flow.get('error', 'Onbekende fout'))}

        return {
            'user_code': flow.get('user_code', ''),
            'verification_uri': flow.get('verification_uri', 'https://microsoft.com/devicelogin'),
            'message': flow.get('message', ''),
            'expires_in': flow.get('expires_in', 900),
            'interval': flow.get('interval', 5),
            'flow': flow,
        }

    def complete_device_flow(self, flow):
        """Wacht tot de gebruiker de device code heeft ingevoerd.

        Args:
            flow: Het flow-object uit start_device_flow()

        Returns: {access_token, refresh_token, expires_at} of {error: ...}
        """
        app = self._get_msal()
        result = app.acquire_token_by_device_flow(flow)

        if 'error' in result:
            return {'error': result.get('error_description', result.get('error', 'Login mislukt'))}

        self.tokens = {
            'access_token': result.get('access_token', ''),
            'refresh_token': result.get('refresh_token', ''),
            'expires_at': time.time() + result.get('expires_in', 3600),
            'id_token_claims': result.get('id_token_claims', {}),
        }
        return self.tokens

    # ------------------------------------------------------------------
    # Auth Code Flow (alternatief — redirect-based)
    # ------------------------------------------------------------------
    def get_auth_url(self, redirect_uri='http://localhost:5001/auth/callback'):
        """Genereer de Microsoft login URL.

        Returns: {'url': 'https://login.microsoftonline.com/...', 'state': '...'}
        """
        if not self.client_id:
            return {'error': 'Geen Microsoft Client ID ingesteld.'}

        app = self._get_msal()
        flow = app.initiate_auth_code_flow(
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )
        return {
            'url': flow.get('auth_uri', ''),
            'flow': flow,
        }

    def complete_auth_code(self, flow, auth_response):
        """Verwerk de OAuth callback.

        Args:
            flow: Het flow-object uit get_auth_url()
            auth_response: De query parameters van de callback URL

        Returns: {access_token, refresh_token, expires_at}
        """
        app = self._get_msal()
        result = app.acquire_token_by_auth_code_flow(flow, auth_response)

        if 'error' in result:
            return {'error': result.get('error_description', result.get('error', 'Login mislukt'))}

        self.tokens = {
            'access_token': result.get('access_token', ''),
            'refresh_token': result.get('refresh_token', ''),
            'expires_at': time.time() + result.get('expires_in', 3600),
            'id_token_claims': result.get('id_token_claims', {}),
        }
        return self.tokens

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------
    def refresh_token(self):
        """Vernieuw het access token met het refresh token."""
        if not self.tokens.get('refresh_token'):
            return {'error': 'Geen refresh token beschikbaar. Log opnieuw in.'}

        app = self._get_msal()
        # MSAL cache-based refresh
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(SCOPES, account=accounts[0])
            if result and 'access_token' in result:
                self.tokens['access_token'] = result['access_token']
                self.tokens['expires_at'] = time.time() + result.get('expires_in', 3600)
                return self.tokens

        return {'error': 'Token vernieuwen mislukt. Log opnieuw in.'}

    def _ensure_token(self):
        """Zorg dat we een geldig access token hebben."""
        if not self.tokens.get('access_token'):
            raise RuntimeError('Niet ingelogd bij Outlook')

        # Check of token bijna verlopen is (5 min marge)
        if self.tokens.get('expires_at', 0) < time.time() + 300:
            result = self.refresh_token()
            if 'error' in result:
                raise RuntimeError(result['error'])

    def _headers(self):
        """Auth headers voor Graph API calls."""
        self._ensure_token()
        return {
            'Authorization': f'Bearer {self.tokens["access_token"]}',
            'Content-Type': 'application/json',
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def is_connected(self):
        """Check of er een geldig token is."""
        if not self.tokens.get('access_token'):
            return False
        if self.tokens.get('expires_at', 0) < time.time():
            # Probeer te refreshen
            result = self.refresh_token()
            return 'error' not in result
        return True

    def get_profile(self):
        """Haal het profiel van de ingelogde gebruiker op."""
        try:
            r = http_requests.get(f'{GRAPH_BASE}/me', headers=self._headers(), timeout=10)
            if r.status_code == 200:
                data = r.json()
                return {
                    'name': data.get('displayName', ''),
                    'email': data.get('mail', '') or data.get('userPrincipalName', ''),
                }
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # E-mail zoeken
    # ------------------------------------------------------------------
    def search_emails(self, query, top=50, skip=0, from_date=None, to_date=None):
        """Zoek e-mails via Microsoft Graph API.

        Args:
            query: Zoektekst (wordt gezocht in onderwerp, body, afzender)
            top: Max aantal resultaten
            skip: Offset voor paginering
            from_date: Alleen mails na deze datum (datetime of string YYYY-MM-DD)
            to_date: Alleen mails voor deze datum

        Returns: lijst van e-mail dicts
        """
        params = {
            '$top': min(top, 100),
            '$skip': skip,
            '$orderby': 'receivedDateTime desc',
            '$select': 'id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,'
                       'bodyPreview,hasAttachments,importance,isRead,conversationId,internetMessageId,'
                       'body',
        }

        # Bouw filter
        filters = []
        if from_date:
            if isinstance(from_date, str):
                from_date = from_date + 'T00:00:00Z' if 'T' not in from_date else from_date
            else:
                from_date = from_date.isoformat() + 'Z'
            filters.append(f"receivedDateTime ge {from_date}")
        if to_date:
            if isinstance(to_date, str):
                to_date = to_date + 'T23:59:59Z' if 'T' not in to_date else to_date
            else:
                to_date = to_date.isoformat() + 'Z'
            filters.append(f"receivedDateTime le {to_date}")

        if filters:
            params['$filter'] = ' and '.join(filters)

        # Search query
        if query:
            params['$search'] = f'"{query}"'
            # Bij $search mag geen $orderby (Graph API beperking)
            if '$search' in params:
                params.pop('$orderby', None)

        url = f'{GRAPH_BASE}/me/messages'
        r = http_requests.get(url, headers=self._headers(), params=params, timeout=30)

        if r.status_code != 200:
            return {'error': f'Graph API fout: {r.status_code} — {r.text[:200]}'}

        data = r.json()
        emails = []
        for msg in data.get('value', []):
            emails.append(_parse_email(msg))

        return {
            'emails': emails,
            'count': len(emails),
            'total': data.get('@odata.count', len(emails)),
            'has_more': '@odata.nextLink' in data,
        }

    def search_emails_for_zaak(self, zaak_naam, wederpartij='', extra_terms=None, days_back=365*3):
        """Zoek e-mails die relevant zijn voor een specifieke zaak.

        Zoekt op combinaties van zaak-naam, wederpartij, en extra termen.
        """
        queries = []

        # Hoofdzoekopdracht: zaaknaam
        if zaak_naam:
            queries.append(zaak_naam)

        # Wederpartij als extra zoekterm
        if wederpartij:
            queries.append(wederpartij)

        # Extra zoektermen (kenmerk, instantie, etc.)
        if extra_terms:
            queries.extend(extra_terms)

        from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

        all_emails = {}  # Dedup op message ID
        for q in queries:
            if not q.strip():
                continue
            result = self.search_emails(q.strip(), top=50, from_date=from_date)
            if 'error' in result:
                continue
            for email in result.get('emails', []):
                mid = email.get('internet_message_id') or email.get('id')
                if mid not in all_emails:
                    all_emails[mid] = email

        # Sorteer op datum (nieuwste eerst)
        emails = sorted(all_emails.values(), key=lambda e: e.get('received', ''), reverse=True)

        return {
            'emails': emails,
            'count': len(emails),
            'queries_used': queries,
        }

    def fetch_all_emails(self, folder='', top_per_page=100, max_emails=5000, since_date=None,
                         progress_callback=None):
        """Haal ALLE e-mails op uit de mailbox (gepagineerd).

        Args:
            folder: Specifieke map ('inbox', 'sentitems', '') — leeg = alle mappen
            top_per_page: Aantal per pagina (max 100)
            max_emails: Maximum totaal aantal
            since_date: Alleen mails na deze datum (YYYY-MM-DD string)
            progress_callback: Functie(fetched, total_estimate) voor voortgang

        Returns: {'emails': [...], 'count': N, 'folders_synced': [...]}
        """
        all_emails = []
        folders_synced = []

        # Bepaal welke folders we ophalen
        if folder:
            folder_list = [folder]
        else:
            folder_list = ['inbox', 'sentitems']  # Ontvangen + verzonden

        for folder_name in folder_list:
            url = f'{GRAPH_BASE}/me/mailFolders/{folder_name}/messages'
            params = {
                '$top': min(top_per_page, 100),
                '$orderby': 'receivedDateTime desc',
                '$select': 'id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,'
                           'bodyPreview,hasAttachments,importance,isRead,conversationId,'
                           'internetMessageId,body',
            }

            if since_date:
                date_filter = since_date + 'T00:00:00Z' if 'T' not in since_date else since_date
                params['$filter'] = f"receivedDateTime ge {date_filter}"

            page = 0
            while url and len(all_emails) < max_emails:
                try:
                    r = http_requests.get(url, headers=self._headers(), params=params if page == 0 else None, timeout=30)
                    if r.status_code != 200:
                        break

                    data = r.json()
                    msgs = data.get('value', [])
                    if not msgs:
                        break

                    for msg in msgs:
                        email = _parse_email(msg, include_body=True)
                        email['_folder'] = folder_name
                        all_emails.append(email)

                    if progress_callback:
                        progress_callback(len(all_emails), max_emails)

                    # Volgende pagina
                    url = data.get('@odata.nextLink', '')
                    page += 1

                    if len(all_emails) >= max_emails:
                        break
                except Exception as e:
                    break

            folders_synced.append(folder_name)

        return {
            'emails': all_emails,
            'count': len(all_emails),
            'folders_synced': folders_synced,
        }

    def get_user_email(self):
        """Haal het e-mailadres van de ingelogde gebruiker op."""
        profile = self.get_profile()
        if profile:
            return profile.get('email', '')
        return ''

    def get_email(self, message_id):
        """Haal een specifiek e-mailbericht op (volledige body)."""
        params = {
            '$select': 'id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,'
                       'body,bodyPreview,hasAttachments,importance,isRead,conversationId,'
                       'internetMessageId',
        }
        url = f'{GRAPH_BASE}/me/messages/{message_id}'
        r = http_requests.get(url, headers=self._headers(), params=params, timeout=15)

        if r.status_code != 200:
            return {'error': f'E-mail niet gevonden: {r.status_code}'}

        return _parse_email(r.json(), include_body=True)

    def get_email_attachments(self, message_id):
        """Haal bijlagen van een e-mail op."""
        url = f'{GRAPH_BASE}/me/messages/{message_id}/attachments'
        r = http_requests.get(url, headers=self._headers(), timeout=15)

        if r.status_code != 200:
            return {'error': f'Bijlagen niet gevonden: {r.status_code}'}

        attachments = []
        for att in r.json().get('value', []):
            attachments.append({
                'id': att.get('id', ''),
                'name': att.get('name', ''),
                'content_type': att.get('contentType', ''),
                'size': att.get('size', 0),
                'is_inline': att.get('isInline', False),
            })
        return {'attachments': attachments}


# ---------------------------------------------------------------------------
# Helper: parse Graph API message naar ons formaat
# ---------------------------------------------------------------------------
def _parse_email(msg, include_body=False):
    """Parse een Graph API message object naar een plat dict."""
    from_data = msg.get('from', {}).get('emailAddress', {})
    to_list = [r.get('emailAddress', {}).get('address', '')
               for r in msg.get('toRecipients', [])]
    cc_list = [r.get('emailAddress', {}).get('address', '')
               for r in msg.get('ccRecipients', [])]

    received = msg.get('receivedDateTime', '')
    sent = msg.get('sentDateTime', '')

    email = {
        'id': msg.get('id', ''),
        'internet_message_id': msg.get('internetMessageId', ''),
        'subject': msg.get('subject', '(geen onderwerp)'),
        'from_name': from_data.get('name', ''),
        'from_email': from_data.get('address', ''),
        'to': to_list,
        'cc': cc_list,
        'received': received,
        'sent': sent,
        'date': received[:10] if received else (sent[:10] if sent else ''),
        'preview': msg.get('bodyPreview', '')[:300],
        'has_attachments': msg.get('hasAttachments', False),
        'importance': msg.get('importance', 'normal'),
        'is_read': msg.get('isRead', True),
        'conversation_id': msg.get('conversationId', ''),
    }

    if include_body:
        body = msg.get('body', {})
        email['body_type'] = body.get('contentType', 'text')
        email['body'] = body.get('content', '')

    return email


# ---------------------------------------------------------------------------
# Setup instructies
# ---------------------------------------------------------------------------
SETUP_INSTRUCTIONS = """
## Microsoft Outlook koppelen

Om e-mails te doorzoeken moet u eenmalig een **App-registratie** aanmaken:

### Stap 1: Ga naar Azure Portal
1. Open [https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Log in met uw Microsoft-account (dezelfde als uw Hotmail/Outlook)

### Stap 2: Nieuwe registratie
1. Klik **+ Nieuwe registratie**
2. Naam: `Juridisch Assistent` (of wat u wilt)
3. Ondersteunde accounttypen: **Alleen persoonlijke Microsoft-accounts**
4. Omleidings-URI: kies **Openbare client/systeemeigen (mobiel en desktop)** en vul in: `http://localhost:5001/auth/callback`
5. Klik **Registreren**

### Stap 3: Client ID kopiëren
1. Kopieer de **Toepassings-id (client-id)** van het overzichtscherm
2. Plak deze in de Instellingen van Juridisch Assistent

### Stap 4: API-machtigingen toevoegen
1. Ga naar **API-machtigingen** → **Machtiging toevoegen**
2. Kies **Microsoft Graph** → **Gedelegeerde machtigingen**
3. Voeg toe: `Mail.Read`, `User.Read`
4. Klik **Machtiging verlenen voor ...** (admin consent)

Dat is alles! Daarna kunt u op **Koppel Outlook** klikken.
"""
