"""
Juridisch Assistent — Mijn Rechtspraak Automatisering (Playwright)
Opent een ZICHTBARE browser. Gebruiker logt zelf in met DigiD.
Na login vult de tool formulieren in. NOOIT automatisch indienen.
"""
import asyncio
import threading
import time

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


class RechtspraakAutomation:
    """Browser-automatisering voor Mijn Rechtspraak."""

    MIJN_RECHTSPRAAK_URL = 'https://mijn.rechtspraak.nl/'
    LOGIN_CHECK_INTERVAL = 2  # seconden

    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.status = 'idle'  # idle/launching/waiting_login/logged_in/filling/done/error
        self.error = None
        self._playwright = None
        self._loop = None
        self._thread = None

    @property
    def is_available(self):
        return HAS_PLAYWRIGHT

    def get_status(self):
        """Huidige status van de automatisering."""
        return {
            'status': self.status,
            'error': self.error,
            'available': HAS_PLAYWRIGHT,
            'url': self.page.url if self.page else None,
        }

    # -------------------------------------------------------------------
    # Start/stop
    # -------------------------------------------------------------------
    def start(self):
        """Start browser en navigeer naar Mijn Rechtspraak."""
        if not HAS_PLAYWRIGHT:
            self.status = 'error'
            self.error = 'Playwright niet geinstalleerd. pip install playwright && playwright install chromium'
            return self.get_status()

        self.status = 'launching'
        self.error = None

        # Run in aparte thread zodat Flask niet blokkeert
        self._thread = threading.Thread(target=self._run_async, daemon=True)
        self._thread.start()

        # Wacht even tot de browser start
        time.sleep(3)
        return self.get_status()

    def _run_async(self):
        """Async Playwright loop in aparte thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._launch_browser())

    async def _launch_browser(self):
        """Launch zichtbare browser en navigeer naar login."""
        try:
            self._playwright = await async_playwright().start()
            self.browser = await self._playwright.chromium.launch(
                headless=False,  # ALTIJD zichtbaar — gebruiker moet kunnen zien
                args=['--start-maximized']
            )
            self.context = await self.browser.new_context(
                viewport=None,  # Gebruik venstergrootte
                locale='nl-NL',
                timezone_id='Europe/Amsterdam',
            )
            self.page = await self.context.new_page()

            # Navigeer naar Mijn Rechtspraak
            await self.page.goto(self.MIJN_RECHTSPRAAK_URL, wait_until='networkidle')
            self.status = 'waiting_login'

            # Wacht tot gebruiker inlogt met DigiD
            await self._wait_for_login()

        except Exception as e:
            self.status = 'error'
            self.error = str(e)

    async def _wait_for_login(self):
        """Poll tot DigiD login compleet is."""
        max_wait = 300  # 5 minuten max
        elapsed = 0

        while elapsed < max_wait:
            try:
                url = self.page.url
                # Na login redirect Mijn Rechtspraak naar een dashboard/overzicht
                if 'mijn.rechtspraak.nl' in url and '/login' not in url.lower() and 'digid' not in url.lower():
                    # Check voor typische post-login elementen
                    try:
                        await self.page.wait_for_selector('[class*="dashboard"], [class*="overzicht"], [class*="welkom"], main', timeout=3000)
                        self.status = 'logged_in'
                        return
                    except Exception:
                        pass

                # Check of we nog op DigiD/login pagina zijn
                if 'digid' in url.lower() or 'login' in url.lower():
                    self.status = 'waiting_login'

            except Exception:
                pass

            await asyncio.sleep(self.LOGIN_CHECK_INTERVAL)
            elapsed += self.LOGIN_CHECK_INTERVAL

        if self.status == 'waiting_login':
            self.status = 'error'
            self.error = 'Timeout: login niet voltooid binnen 5 minuten'

    # -------------------------------------------------------------------
    # Formulier invullen
    # -------------------------------------------------------------------
    async def _fill_form(self, case_data):
        """
        Vul formuliervelden in op Mijn Rechtspraak.
        BELANGRIJK: Klikt NOOIT op 'indienen' — gebruiker moet zelf bevestigen.
        """
        if self.status != 'logged_in':
            return {'ok': False, 'error': 'Niet ingelogd'}

        self.status = 'filling'

        try:
            # Navigeer naar 'Nieuwe zaak' als die knop er is
            try:
                new_case_btn = await self.page.wait_for_selector(
                    'a:has-text("Nieuwe zaak"), button:has-text("Nieuwe zaak"), a:has-text("Zaak starten")',
                    timeout=5000
                )
                if new_case_btn:
                    await new_case_btn.click()
                    await self.page.wait_for_load_state('networkidle')
            except Exception:
                pass

            # Vul beschikbare velden in
            filled = []

            # Probeer standaard veldnamen te vinden en in te vullen
            field_mapping = {
                'onderwerp': case_data.get('onderwerp', ''),
                'omschrijving': case_data.get('omschrijving', ''),
                'toelichting': case_data.get('toelichting', ''),
                'wederpartij': case_data.get('wederpartij', ''),
                'kenmerk': case_data.get('kenmerk', ''),
            }

            for field_name, value in field_mapping.items():
                if not value:
                    continue
                try:
                    # Zoek input/textarea met matching name, id, of label
                    selectors = [
                        f'input[name*="{field_name}" i]',
                        f'textarea[name*="{field_name}" i]',
                        f'input[id*="{field_name}" i]',
                        f'textarea[id*="{field_name}" i]',
                        f'input[placeholder*="{field_name}" i]',
                    ]
                    for sel in selectors:
                        try:
                            el = await self.page.wait_for_selector(sel, timeout=1000)
                            if el:
                                await el.fill(value)
                                filled.append(field_name)
                                break
                        except Exception:
                            continue
                except Exception:
                    continue

            self.status = 'done'
            return {
                'ok': True,
                'filled_fields': filled,
                'message': f'{len(filled)} velden ingevuld. Controleer en bevestig zelf de indiening.'
            }

        except Exception as e:
            self.status = 'error'
            self.error = str(e)
            return {'ok': False, 'error': str(e)}

    def fill(self, case_data):
        """Synchrone wrapper voor formulier invullen."""
        if not self._loop or not self.page:
            return {'ok': False, 'error': 'Browser niet actief'}

        future = asyncio.run_coroutine_threadsafe(
            self._fill_form(case_data),
            self._loop
        )
        try:
            return future.result(timeout=30)
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # -------------------------------------------------------------------
    # Stop
    # -------------------------------------------------------------------
    def stop(self):
        """Sluit browser netjes af."""
        try:
            if self._loop and self.browser:
                future = asyncio.run_coroutine_threadsafe(
                    self._cleanup(),
                    self._loop
                )
                future.result(timeout=10)
        except Exception:
            pass

        self.status = 'idle'
        self.error = None
        return self.get_status()

    async def _cleanup(self):
        """Async cleanup."""
        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass

        self.page = None
        self.context = None
        self.browser = None
        self._playwright = None


# Singleton instance
_automation = None

def get_automation():
    """Haal singleton RechtspraakAutomation op."""
    global _automation
    if _automation is None:
        _automation = RechtspraakAutomation()
    return _automation
