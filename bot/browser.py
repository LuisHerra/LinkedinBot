"""Arranque del navegador: proxy residencial fijo, sesión por cookie y perfil de navegador constante."""
import os
import urllib.request
from contextlib import contextmanager
from urllib.parse import urlsplit

# Huella constante entre ejecuciones: mismo idioma, zona horaria y tamaño de ventana que un PC normal.
VIEWPORT = {"width": 1440, "height": 900}


def proxy_from_url(url):
    """'http://user:pass@host:port' -> dict de proxy de Playwright."""
    p = urlsplit(url)
    proxy = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        proxy["username"] = p.username
        proxy["password"] = p.password or ""
    return proxy


def normalize_proxy(raw):
    """Limpia el valor del secreto: espacios, comillas, saltos de línea y esquema ausente."""
    url = (raw or "").strip().strip("\"'").strip()
    if url and "://" not in url:
        url = "http://" + url
    return url


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
        print(f"Proxy {name} {describe(url)} falla: {error}")
    raise RuntimeError("Ningún proxy responde (PROXY_PRIMARY / PROXY_SECONDARY). No se ejecuta sin proxy.")


@contextmanager
def open_page(cfg, proxy_url):
    from playwright.sync_api import sync_playwright

    # Tolera que el secreto se haya pegado como "li_at=valor;" o con espacios/comillas.
    li_at = os.environ["LI_AT"].strip().strip("\"'").removeprefix("li_at=").rstrip(";").strip()
    with sync_playwright() as pw:
        # headless=False: en GitHub Actions se ejecuta dentro de xvfb-run, con navegador "con ventana".
        browser = pw.chromium.launch(headless=os.environ.get("HEADLESS") == "1",
                                     proxy=proxy_from_url(normalize_proxy(proxy_url)))
        context = browser.new_context(
            locale="es-ES",
            timezone_id=cfg["timezone"],
            viewport=VIEWPORT,
        )
        context.add_cookies([{
            "name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/",
            "httpOnly": True, "secure": True, "sameSite": "None",
        }])
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
