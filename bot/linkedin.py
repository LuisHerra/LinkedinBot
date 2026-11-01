"""Acciones sobre la web de LinkedIn.

Los selectores se basan en roles y aria-labels (español e inglés), que cambian menos que las clases CSS,
pero LinkedIn rediseña a menudo: si algo deja de funcionar, la captura en data/screenshots dice dónde.
"""
import os
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


# "Conectar" en la tarjeta del perfil es un enlace a /preload/custom-invite/ (diseño 2026) o un botón (diseño antiguo).
CONNECT_CSS = ("a[href*='custom-invite']:visible, button[aria-label*='a conectar' i]:visible, "
               "button[aria-label*='to connect' i]:visible")

TOP_CARD_JS = r"""() => {
    const sec = document.querySelector('main section');
    if (!sec) return null;
    const visible = el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const label = el => (el.getAttribute('aria-label') || el.innerText || '').trim();
    const ctrls = [...sec.querySelectorAll('a, button, [role="button"]')].filter(visible);
    const any = re => ctrls.some(el => re.test(label(el)));
    return {
        connect: ctrls.some(el => /custom-invite/.test(el.getAttribute('href') || ''))
                 || any(/(a conectar|to connect)$|^(conectar|connect)$/i),
        follow: any(/^(seguir|follow)\b/i),
        pending: any(/pendiente|pending/i),
        more: any(/^(más|more)$/i),
    };
}"""

DEGREE_LINE = re.compile(r"^[•·]?\s*\d+\s*(º|er|nd|rd|th)\b")
MUTUAL_LINE = re.compile(r"mutual|en com[uú]n", re.I)
UI_LINES = re.compile(r"^(conectar|connect|seguir|follow|mensaje|message|pendiente|pending)$", re.I)


def parse_search_card(href, text):
    """Tarjeta de resultado -> {profile_url, name, headline, mutuals} o None.

    Texto típico: nombre / • 2º / titular / ubicación / Conectar / Resumen… / "X y 8 contactos más en común".
    """
    url = clean_profile_url(href)
    if not url:
        return None
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return None
    meta = [l for l in lines[1:] if not DEGREE_LINE.match(l) and not UI_LINES.match(l)]
    mutual_lines = [l for l in lines if MUTUAL_LINE.search(l)]
    return {
        "profile_url": url,
        "name": lines[0],
        "headline": meta[0] if meta else None,
        # Solo la línea de contactos en común: el resumen puede contener números o "y".
        "mutuals": parse_mutuals(mutual_lines[-1]) if mutual_lines else 0,
    }


class NotLoggedIn(Exception):
    pass


class Checkpoint(Exception):
    """LinkedIn pide verificación (captcha, código, identidad). El bot se detiene; lo resuelves tú a mano."""


class WeeklyLimitReached(Exception):
    pass


class ProfileUnavailable(Exception):
    """Un perfil concreto no ha cargado; se salta sin abortar la tanda."""


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

    def goto(self, url, attempts=3):
        """Navega tolerando la lentitud del proxy: basta con que llegue la respuesta y aparezca <main>.

        LinkedIn es una SPA que mantiene conexiones abiertas; esperar a 'domcontentloaded' a través
        de un proxy residencial puede no completarse nunca aunque la página ya sea usable.
        """
        from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout

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
            except PWError as e:
                # Bucle feed <-> login: LinkedIn ha invalidado la sesión de la cookie.
                if "ERR_TOO_MANY_REDIRECTS" in str(e):
                    raise NotLoggedIn(f"bucle de redirecciones en {url}") from None
                # La página anterior aún estaba redirigiendo por su cuenta (SPA): se espera y se reintenta.
                if "interrupted by another navigation" in str(e) and attempt < attempts:
                    print(f"  (navegación interrumpida, reintentando {url.split('?')[0]})")
                    self.page.wait_for_timeout(self.rng.randint(2500, 5000))
                    continue
                raise
        try:
            self.page.wait_for_selector("main", timeout=45_000)
        except PWTimeout:
            pass  # check_session decide si es login/checkpoint; si no, el siguiente paso fallará con captura
        self.page.wait_for_timeout(self.rng.randint(1500, 3500))
        self.check_session()

    def check_session(self):
        url = self.page.url
        # "¡Hola de nuevo!" vive bajo /checkpoint/lg pero es un login normal, no una verificación.
        if "/checkpoint/lg" in url or self.page.locator("input[type='password']").count():
            self.screenshot("not-logged-in")
            raise NotLoggedIn(url)
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
        if os.environ.get("DEBUG_SHOTS"):
            self.screenshot(f"search-p{page_num}")
        # Cada resultado es un [role=listitem] (antes <li>). El primer enlace /in/ es la persona;
        # los siguientes son tus contactos en común, que no deben tratarse como candidatos.
        cards = self.page.evaluate("""() => {
            const out = [];
            let items = [...document.querySelectorAll('[role="listitem"]')];
            if (!items.length) items = [...document.querySelectorAll('main li')];
            for (const card of items) {
                const a = card.querySelector('a[href*="/in/"]');
                if (!a) continue;
                out.push({href: a.getAttribute('href'), text: card.innerText});
            }
            return out;
        }""")
        return [c for c in (parse_search_card(x["href"], x["text"]) for x in cards) if c]

    # --- perfil ---
    def top_card(self):
        return self.page.locator("main section").first

    def top_card_state(self):
        """Controles *visibles* de la tarjeta superior del perfil.

        El menú "Más" tiene elementos ocultos (p. ej. "Seguir a X") que no deben contar como botón principal.
        """
        return self.page.evaluate(TOP_CARD_JS) or {"connect": False, "follow": False, "pending": False, "more": False}

    def profile_text(self, profile_url, attempts=2):
        """Texto de <main> del perfil; en máquinas lentas o con proxy puede tardar, así que se recarga una vez."""
        from playwright.sync_api import TimeoutError as PWTimeout

        for attempt in range(1, attempts + 1):
            try:
                return self.page.locator("main").inner_text(timeout=30_000)
            except PWTimeout:
                if attempt == attempts:
                    self.screenshot("profile-timeout")
                    raise ProfileUnavailable(profile_url) from None
                print(f"  (perfil lento, recargando {profile_url})")
                self.goto(profile_url)

    def inspect_profile(self, profile_url):
        from playwright.sync_api import Error as PWError

        try:
            self.goto(profile_url)
        except PWError as e:  # timeouts y navegaciones fallidas de un perfil concreto
            self.screenshot("profile-error")
            raise ProfileUnavailable(f"{profile_url}: {str(e).splitlines()[0][:120]}") from None
        if os.environ.get("DEBUG_SHOTS"):
            self.screenshot("profile")
        self.scroll_a_bit(self.rng.randint(2, 4))
        text = self.profile_text(profile_url)
        if parse_followers(text) is None:
            # El número de seguidores está en la sección "Actividad", que se carga al hacer scroll;
            # en la VM tarda más, así que se espera un poco antes de darlo por desconocido.
            try:
                self.page.wait_for_function(
                    "() => /seguidores|followers/i.test(document.querySelector('main')?.innerText || '')",
                    timeout=10_000,
                )
                text = self.page.locator("main").inner_text(timeout=10_000)
            except Exception:
                pass
        state = self.top_card_state()
        return {
            "followers": parse_followers(text),
            # Texto del perfil (extracto, experiencia, educación) para detectar afinidad. No se guarda en la base de datos.
            "profile_text": text[:6000],
            # Modo creador: el botón principal visible es "Seguir" y "Conectar" queda escondido en "Más".
            "creator": state["follow"] and not state["connect"],
            # Si no se ve "Conectar", puede estar en el menú "Más"; solo se descarta si ya hay invitación pendiente.
            "can_connect": not state["pending"],
        }

    def connect_control(self):
        """Botón/enlace "Conectar" visible de la tarjeta superior (o, si no, el del menú "Más")."""
        card = self.top_card()
        direct = card.locator(CONNECT_CSS).first
        if direct.count():
            return direct
        more = card.locator("button:visible").filter(has_text=re.compile(r"^\s*(Más|More)\s*$")).first
        if not more.count():
            return None
        more.click()
        self.page.wait_for_timeout(self.rng.randint(600, 1400))
        in_menu = self.page.locator(
            "a[href*='custom-invite']:visible, [role='menu'] [aria-label*='conectar' i]:visible, "
            "[role='menu'] [aria-label*='connect' i]:visible, [role='menuitem']:visible:has-text('Conectar')"
        ).first
        if in_menu.count():
            return in_menu
        self.page.keyboard.press("Escape")
        return None

    def send_invite(self, profile_url, dry_run=False):
        """Envía la invitación sin nota. Devuelve (ok, motivo)."""
        from playwright.sync_api import Error as PWError

        if self.page.url.rstrip("/") != profile_url.rstrip("/"):
            try:
                self.goto(profile_url)
            except PWError as e:
                raise ProfileUnavailable(f"{profile_url}: {str(e).splitlines()[0][:120]}") from None
        self.profile_text(profile_url)  # asegura que la tarjeta ha cargado antes de buscar "Conectar"
        # Tras el scroll de la inspección, la cabecera fija aparece y se anima, y el botón de la tarjeta
        # nunca llega a estar "estable" para Playwright. Se vuelve arriba y se deja asentar la página.
        self.page.evaluate("window.scrollTo(0, 0)")
        self.page.wait_for_timeout(self.rng.randint(1500, 3000))
        card = self.top_card()
        button = self.connect_control()
        if button is None:
            return False, "conectar no disponible"
        if dry_run:
            # No se pulsa: en algunos perfiles "Conectar" envía directamente sin diálogo.
            self.page.keyboard.press("Escape")
            return True, f"dry-run (encontrado: {button.get_attribute('aria-label') or button.inner_text()})"
        from playwright.sync_api import TimeoutError as PWTimeout

        # "Conectar" es un enlace a /preload/custom-invite/: Playwright esperaría a que esa navegación termine,
        # y en una máquina lenta con proxy eso supera el timeout aunque el diálogo ya esté abriéndose.
        href = button.get_attribute("href") or ""
        try:
            button.click(no_wait_after=True, timeout=20_000)
        except PWTimeout:
            if "custom-invite" not in href:
                raise ProfileUnavailable(f"{profile_url}: el botón Conectar no responde") from None
            # Mismo destino que el clic: abrir el enlace de invitación directamente.
            print("  (el clic no entra; abriendo el enlace de invitación)")
            self.goto(BASE + href if href.startswith("/") else href)
        try:
            self.page.wait_for_selector("[role='dialog']:visible, [role='alertdialog']:visible", timeout=45_000)
        except PWTimeout:
            pass  # algunos perfiles envían directamente sin diálogo; se comprueba abajo
        self.page.wait_for_timeout(self.rng.randint(1200, 2500))

        body = self.page.locator("body").inner_text()
        if WEEKLY_LIMIT.search(body):
            self.screenshot("weekly-limit")
            raise WeeklyLimitReached()
        dialog = self.page.locator("[role='dialog']:visible, [role='alertdialog']:visible")
        if dialog.count():
            if EMAIL_REQUIRED.search(dialog.first.inner_text()):
                self.page.keyboard.press("Escape")
                return False, "pide email"
            send = dialog.get_by_role("button", name=SEND_NO_NOTE)
            if not send.count():
                self.screenshot("dialog-unknown")
                self.page.keyboard.press("Escape")
                return False, "diálogo desconocido"
            send.first.click(no_wait_after=True, timeout=60_000)
            try:
                dialog.first.wait_for(state="hidden", timeout=30_000)
            except PWTimeout:
                pass
            self.page.wait_for_timeout(self.rng.randint(1500, 3000))

        body = self.page.locator("body").inner_text()
        if WEEKLY_LIMIT.search(body):
            self.screenshot("weekly-limit")
            raise WeeklyLimitReached()
        # Si el envío dejó la página en /preload/custom-invite/, se vuelve al perfil para verificar "Pendiente".
        if "custom-invite" in self.page.url:
            self.goto(profile_url)
            self.profile_text(profile_url)
        if self.top_card_state()["pending"]:
            return True, "enviada"
        # Sin diálogo y sin "Pendiente": no se puede afirmar que se envió. No se registra como enviada;
        # si en realidad salió, la próxima sincronización de pendientes la detectará y la contará.
        self.screenshot("unconfirmed")
        return False, "no confirmada (sin diálogo ni estado pendiente)"
