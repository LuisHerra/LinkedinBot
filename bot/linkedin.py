"""Acciones sobre la web de LinkedIn.

Los selectores se basan en roles y aria-labels (español e inglés), que cambian menos que las clases CSS,
pero LinkedIn rediseña a menudo: si algo deja de funcionar, la captura en data/screenshots dice dónde.
"""
import re
import time
from urllib.parse import quote

from .config import SCREENSHOTS
from .parsing import clean_profile_url, parse_days_ago, parse_followers, parse_mutuals

BASE = "https://www.linkedin.com"
CONNECT_LABEL = re.compile(r"(invita.* a conectar|invite .* to connect|^conectar$|^connect$)", re.I)
FOLLOW_LABEL = re.compile(r"^(seguir|follow)\b", re.I)
MORE_LABEL = re.compile(r"(más acciones|more actions|^más$|^more$)", re.I)
SEND_NO_NOTE = re.compile(r"(enviar sin nota|send without a note|^enviar$|^send$)", re.I)
WITHDRAW = re.compile(r"^(retirar|withdraw)$", re.I)
PENDING = re.compile(r"(pendiente|pending)", re.I)
WEEKLY_LIMIT = re.compile(r"(límite semanal|weekly invitation limit|weekly limit)", re.I)
EMAIL_REQUIRED = re.compile(r"(dirección de correo|email address to connect|conoce .* correo)", re.I)


class NotLoggedIn(Exception):
    pass


class Checkpoint(Exception):
    """LinkedIn pide verificación (captcha, código, identidad). El bot se detiene; lo resuelves tú a mano."""


class WeeklyLimitReached(Exception):
    pass


class LinkedIn:
    def __init__(self, page, cfg, rng):
        self.page = page
        self.cfg = cfg
        self.rng = rng

    # --- utilidades ---
    def pause(self, key="delay_between_pages_s"):
        lo, hi = self.cfg["schedule"][key]
        time.sleep(self.rng.uniform(lo, hi))

    def scroll_a_bit(self, times=None):
        for _ in range(times or self.rng.randint(1, 4)):
            self.page.mouse.wheel(0, self.rng.randint(250, 700))
            time.sleep(self.rng.uniform(0.6, 1.8))

    def screenshot(self, name):
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOTS / f"{int(time.time())}-{name}.png"
        try:
            self.page.screenshot(path=str(path), full_page=False)
        except Exception:
            pass
        return path

    def goto(self, url, attempts=2):
        """Navega tolerando la lentitud del proxy: basta con que llegue la respuesta y aparezca <main>.

        LinkedIn es una SPA que mantiene conexiones abiertas; esperar a 'domcontentloaded' a través
        de un proxy residencial puede no completarse nunca aunque la página ya sea usable.
        """
        from playwright.sync_api import TimeoutError as PWTimeout

        for attempt in range(1, attempts + 1):
            try:
                self.page.goto(url, wait_until="commit", timeout=60_000)
                break
            except PWTimeout:
                if attempt == attempts:
                    self.screenshot("goto-timeout")
                    raise
                print(f"  (reintentando carga de {url.split('?')[0]})")
                self.page.wait_for_timeout(self.rng.randint(4000, 8000))
        try:
            self.page.wait_for_selector("main", timeout=45_000)
        except PWTimeout:
            pass  # check_session decide si es login/checkpoint; si no, el siguiente paso fallará con captura
        self.page.wait_for_timeout(self.rng.randint(1500, 3500))
        self.check_session()

    def check_session(self):
        url = self.page.url
        if "/checkpoint" in url or "/challenge" in url or self.page.locator("iframe[src*='captcha']").count():
            self.screenshot("checkpoint")
            raise Checkpoint(url)
        if "/login" in url or "/authwall" in url or "/uas/login" in url:
            self.screenshot("not-logged-in")
            raise NotLoggedIn(url)

    def ensure_logged_in(self):
        self.goto(f"{BASE}/feed/")
        self.scroll_a_bit()

    # --- invitaciones enviadas ---
    def pending_invitations(self, max_pages=6):
        """Lista de invitaciones pendientes [{profile_url, name, days_ago}] (las tuyas manuales incluidas)."""
        self.goto(f"{BASE}/mynetwork/invitation-manager/sent/")
        results, seen = [], set()
        for _ in range(max_pages):
            self.scroll_a_bit(6)
            items = self.page.evaluate("""() => {
                const out = [];
                for (const a of document.querySelectorAll('main a[href*="/in/"]')) {
                    const card = a.closest('li') || a.closest('[role="listitem"]') || a.parentElement?.parentElement;
                    if (!card) continue;
                    out.push({href: a.getAttribute('href'), name: a.innerText.trim(), text: card.innerText});
                }
                return out;
            }""")
            for it in items:
                url = clean_profile_url(it["href"])
                if not url or url in seen:
                    continue
                days = parse_days_ago(it["text"])
                if days is None:
                    continue
                seen.add(url)
                name = it["name"].split("\n")[0] or None
                results.append({"profile_url": url, "name": name, "days_ago": days})
            nxt = self.page.get_by_role("button", name=re.compile(r"^(siguiente|next)$", re.I))
            if nxt.count() and nxt.first.is_enabled():
                nxt.first.click()
                self.page.wait_for_timeout(self.rng.randint(2000, 4000))
            else:
                break
        return results

    def find_sent_card(self, slug, max_pages=6):
        self.goto(f"{BASE}/mynetwork/invitation-manager/sent/")
        for _ in range(max_pages):
            self.scroll_a_bit(6)
            card = self.page.locator(f"li:has(a[href*='/in/{slug}'])").first
            if card.count():
                return card
            nxt = self.page.get_by_role("button", name=re.compile(r"^(siguiente|next)$", re.I))
            if not (nxt.count() and nxt.first.is_enabled()):
                return None
            nxt.first.click()
            self.page.wait_for_timeout(self.rng.randint(2000, 4000))
        return None

    def withdraw(self, profile_url):
        """Retira una invitación pendiente desde el gestor de enviadas."""
        slug = profile_url.rstrip("/").split("/")[-1]
        card = self.find_sent_card(slug)
        if card is None:
            return False
        card.scroll_into_view_if_needed()
        card.get_by_role("button", name=WITHDRAW).first.click()
        self.page.wait_for_timeout(self.rng.randint(800, 1800))
        dialog = self.page.get_by_role("dialog")
        if dialog.count():
            dialog.get_by_role("button", name=WITHDRAW).first.click()
        self.page.wait_for_timeout(self.rng.randint(1500, 3000))
        return True

    # --- búsqueda de candidatos ---
    def search(self, keyword, page_num=1, geo_urns=()):
        url = f"{BASE}/search/results/people/?keywords={quote(keyword)}&network=%5B%22S%22%5D&origin=FACETED_SEARCH"
        if geo_urns:
            url += "&geoUrn=" + quote("[" + ",".join(f'"{g}"' for g in geo_urns) + "]")
        if page_num > 1:
            url += f"&page={page_num}"
        self.goto(url)
        self.scroll_a_bit(self.rng.randint(3, 6))
        cards = self.page.evaluate("""() => {
            const out = [];
            for (const a of document.querySelectorAll('main a[href*="/in/"]')) {
                const card = a.closest('li') || a.closest('[data-chameleon-result-urn]') || a.closest('[data-view-name]');
                if (!card) continue;
                out.push({href: a.getAttribute('href'), text: card.innerText});
            }
            return out;
        }""")
        found = {}
        for c in cards:
            url = clean_profile_url(c["href"])
            if not url or url in found:
                continue
            lines = [l.strip() for l in c["text"].split("\n") if l.strip()]
            # Las líneas de la tarjeta suelen ser: nombre, "• 2º", titular, ubicación, contactos en común...
            meta = [l for l in lines[1:] if not re.match(r"^[•·]?\s*\d+(º|er|nd|rd|th)", l) and "Conectar" not in l]
            found[url] = {
                "profile_url": url,
                "name": lines[0] if lines else None,
                "headline": meta[0] if meta else None,
                "mutuals": parse_mutuals(c["text"]),
            }
        return list(found.values())

    # --- perfil ---
    def top_card(self):
        return self.page.locator("main section").first

    def inspect_profile(self, profile_url):
        self.goto(profile_url)
        self.scroll_a_bit(self.rng.randint(1, 3))
        text = self.page.locator("main").inner_text(timeout=15_000)
        card = self.top_card()
        connect_visible = card.get_by_role("button", name=CONNECT_LABEL).count() > 0
        follow_primary = card.get_by_role("button", name=FOLLOW_LABEL).count() > 0 and not connect_visible
        pending = card.get_by_role("button", name=PENDING).count() > 0
        return {
            "followers": parse_followers(text),
            # Texto del perfil (extracto, experiencia, educación) para detectar afinidad. No se guarda en la base de datos.
            "profile_text": text[:6000],
            "creator": follow_primary,
            # Si no se ve "Conectar", puede estar en el menú "Más"; solo se descarta si ya hay invitación pendiente.
            "can_connect": not pending,
        }

    def send_invite(self, profile_url, dry_run=False):
        """Envía la invitación sin nota. Devuelve (ok, motivo)."""
        if self.page.url.rstrip("/") != profile_url.rstrip("/"):
            self.goto(profile_url)
        card = self.top_card()
        button = card.get_by_role("button", name=CONNECT_LABEL)
        if not button.count():
            more = card.get_by_role("button", name=MORE_LABEL)
            if not more.count():
                return False, "sin botón conectar ni menú más"
            more.first.click()
            self.page.wait_for_timeout(self.rng.randint(600, 1400))
            button = self.page.locator("[role='menu'], .artdeco-dropdown__content").get_by_role("button", name=CONNECT_LABEL)
            if not button.count():
                button = self.page.get_by_role("menuitem", name=CONNECT_LABEL)
            if not button.count():
                self.page.keyboard.press("Escape")
                return False, "conectar no disponible en el menú"
        if dry_run:
            return True, "dry-run"
        button.first.click()
        self.page.wait_for_timeout(self.rng.randint(1200, 2500))

        body = self.page.locator("body").inner_text()
        if WEEKLY_LIMIT.search(body):
            self.screenshot("weekly-limit")
            raise WeeklyLimitReached()
        dialog = self.page.get_by_role("dialog")
        if dialog.count():
            if EMAIL_REQUIRED.search(dialog.first.inner_text()):
                self.page.keyboard.press("Escape")
                return False, "pide email"
            send = dialog.get_by_role("button", name=SEND_NO_NOTE)
            if not send.count():
                self.screenshot("dialog-unknown")
                self.page.keyboard.press("Escape")
                return False, "diálogo desconocido"
            send.first.click()
            self.page.wait_for_timeout(self.rng.randint(1500, 3000))

        body = self.page.locator("body").inner_text()
        if WEEKLY_LIMIT.search(body):
            self.screenshot("weekly-limit")
            raise WeeklyLimitReached()
        if card.get_by_role("button", name=PENDING).count():
            return True, "enviada"
        self.screenshot("unconfirmed")
        return True, "enviada (sin confirmar estado pendiente)"
