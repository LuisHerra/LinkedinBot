"""Arranque del navegador: proxy residencial fijo, sesión por cookie y perfil de navegador constante."""
import os
import sys
import urllib.request
from contextlib import contextmanager
from urllib.parse import quote, unquote, urlsplit

# Huella constante entre ejecuciones: mismo idioma, zona horaria y tamaño de ventana que un PC normal.
VIEWPORT = {"width": 1440, "height": 900}


def proxy_from_url(url):
    """'http://user:pass@host:port' -> dict de proxy de Playwright."""
    p = urlsplit(url)
    proxy = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        proxy["username"] = unquote(p.username)
        proxy["password"] = unquote(p.password or "")
    return proxy


def normalize_proxy(raw):
    """Limpia el valor del secreto: espacios, comillas, saltos de línea y esquema ausente.

    Acepta también el formato de muchos proveedores 'ip:puerto:usuario:contraseña'.
    """
    url = (raw or "").strip().strip("\"'").strip()
    if not url:
        return url
    scheme, _, rest = url.rpartition("://")
    scheme = scheme or "http"
    parts = rest.split(":")
    if "@" not in rest and len(parts) == 4:
        host, port, user, password = parts
        rest = f"{user}:{password}@{host}:{port}"
    # Si el secreto solo trae ip:puerto, usa las credenciales comunes de PROXY_USER / PROXY_PASS.
    user, password = os.environ.get("PROXY_USER", "").strip(), os.environ.get("PROXY_PASS", "").strip()
    if "@" not in rest and user:
        rest = f"{quote(user, safe='')}:{quote(password, safe='')}@{rest}"
    return f"{scheme}://{rest}"


def describe(url):
    """host:puerto sin credenciales, para los logs."""
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}:{p.port}" + (" (con usuario)" if p.username else " (SIN usuario)")


def proxy_alive(url, timeout=15):
    """Devuelve (ip_salida, None) o (None, motivo del fallo)."""
    p = urlsplit(url)
    if not p.hostname or not p.port:
        return None, "formato no válido, se espera http://usuario:contraseña@host:puerto"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": url, "https": url}))
        with opener.open("https://api.ipify.org", timeout=timeout) as r:
            return r.read().decode().strip(), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:150]}"


def choose_proxy(cfg, iso_week):
    """Devuelve (nombre, url, ip_salida). En modo sticky se usa siempre el principal; el secundario es solo de reserva."""
    order = [("primary", os.environ.get("PROXY_PRIMARY")), ("secondary", os.environ.get("PROXY_SECONDARY"))]
    if cfg["proxy"]["mode"] == "weekly" and int(iso_week.split("W")[1]) % 2 == 1:
        order.reverse()
    for name, raw in order:
        url = normalize_proxy(raw)
        if not url:
            print(f"Proxy {name}: secreto vacío o no definido")
            continue
        ip, error = proxy_alive(url)
        if ip:
            return name, url, ip
        hint = ""
        if "407" in (error or "") and not urlsplit(url).username:
            hint = " → el secreto solo tiene ip:puerto; añade los secretos PROXY_USER y PROXY_PASS"
        print(f"Proxy {name} {describe(url)} falla: {error}{hint}")
    raise RuntimeError("Ningún proxy responde (PROXY_PRIMARY / PROXY_SECONDARY). No se ejecuta sin proxy.")


CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

# Cookies de vida muy corta que el navegador regenera solo; reutilizarlas caducadas no aporta nada.
VOLATILE_COOKIES = {"__cf_bm", "lidc"}


def parse_cookie_header(header):
    """'a=1; b="x y"; c=3' (cabecera Cookie copiada de DevTools) -> lista de cookies de Playwright."""
    cookies = []
    for part in header.strip().strip("\"'").split(";"):
        name, sep, value = part.strip().partition("=")
        if not sep or not name or name in VOLATILE_COOKIES:
            continue
        cookies.append({
            "name": name, "value": value, "domain": ".linkedin.com", "path": "/",
            "secure": True, "sameSite": "None", "httpOnly": name in {"li_at", "bscookie"},
        })
    return cookies


def session_cookies():
    """LI_COOKIES (todas las cookies del perfil de Chrome) si existe; si no, solo LI_AT.

    Con todas las cookies (bcookie, bscookie, JSESSIONID...) LinkedIn ve el mismo dispositivo en el
    que iniciaste sesión; con li_at suelta parece un dispositivo nuevo y puede invalidar la sesión.
    """
    header = os.environ.get("LI_COOKIES", "").strip()
    if header:
        cookies = parse_cookie_header(header)
        if any(c["name"] == "li_at" for c in cookies):
            return cookies
        print("Aviso: LI_COOKIES no contiene li_at; se ignora y se usa LI_AT")
    # Tolera que el secreto se haya pegado como "li_at=valor;" o con espacios/comillas.
    li_at = os.environ["LI_AT"].strip().strip("\"'").removeprefix("li_at=").rstrip(";").strip()
    return parse_cookie_header(f"li_at={li_at}")


@contextmanager
def open_page(cfg, proxy_url):
    from playwright.sync_api import sync_playwright

    profile_dir = os.environ.get("CHROME_PROFILE_DIR", "").strip()
    if profile_dir:
        # Perfil persistente: tu Chrome en Windows o el Chromium de Playwright en la VM.
        # La sesión vive en el propio perfil, así que nunca se copian cookies entre máquinas.
        exe = os.environ.get("CHROME_EXE") or next((p for p in CHROME_PATHS if os.path.exists(p)), None)
        # Sin vídeos en reproducción automática y sin /dev/shm: menos CPU y RAM en la e2-micro.
        args = ["--autoplay-policy=user-gesture-required", "--disable-dev-shm-usage"]
        # Modo ligero (por defecto en Linux/VM): sin descargar imágenes. Es lo que más CPU y ancho de banda
        # consume en una máquina pequeña con proxy, y el bot no las necesita.
        light = os.environ.get("LIGHT_BROWSER", "1" if sys.platform.startswith("linux") else "0") == "1"
        if light:
            args.append("--blink-settings=imagesEnabled=false")
        print(f"Sesión: perfil persistente {profile_dir} ({'Chrome' if exe else 'Chromium de Playwright'}"
              f"{', modo ligero' if light else ''})")
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                profile_dir,
                **({"executable_path": exe} if exe else {}),
                headless=False,
                args=args,
                proxy=proxy_from_url(normalize_proxy(proxy_url)),
                locale="es-ES",
                timezone_id=cfg["timezone"],
                viewport=VIEWPORT,
            )
            page = context.pages[0] if context.pages else context.new_page()
            try:
                yield page
            finally:
                context.close()
        return

    cookies = session_cookies()
    user_agent = os.environ.get("LI_USER_AGENT", "").strip() or None
    print(f"Sesión: {len(cookies)} cookies, user agent {'propio' if user_agent else 'por defecto de Chromium'}")
    with sync_playwright() as pw:
        # headless=False: en GitHub Actions se ejecuta dentro de xvfb-run, con navegador "con ventana".
        browser = pw.chromium.launch(headless=os.environ.get("HEADLESS") == "1",
                                     proxy=proxy_from_url(normalize_proxy(proxy_url)))
        context = browser.new_context(
            locale="es-ES",
            timezone_id=cfg["timezone"],
            viewport=VIEWPORT,
            user_agent=user_agent,
        )
        context.add_cookies(cookies)
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
