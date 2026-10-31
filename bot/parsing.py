"""Parseo de textos de la interfaz de LinkedIn (español e inglés)."""
import re
from urllib.parse import urlsplit

UNIT_DAYS = {
    "segundo": 0, "minuto": 0, "hora": 0, "second": 0, "minute": 0, "hour": 0,
    "día": 1, "dia": 1, "day": 1,
    "semana": 7, "week": 7,
    "mes": 30, "month": 30,
    "año": 365, "ano": 365, "year": 365,
}
UNIT_RE = "|".join(sorted(UNIT_DAYS, key=len, reverse=True))


def clean_profile_url(href):
    """https://www.linkedin.com/in/fulano-123/?miniProfileUrn=... -> https://www.linkedin.com/in/fulano-123/"""
    parts = urlsplit(href if href.startswith("http") else "https://www.linkedin.com" + href)
    m = re.match(r"(/in/[^/]+)", parts.path)
    return f"https://www.linkedin.com{m.group(1)}/" if m else None


def parse_days_ago(text):
    """'Enviada hace 2 semanas' -> 14, 'Sent 3 days ago' -> 3, 'hoy' -> 0. None si no se reconoce."""
    t = text.lower()
    if re.search(r"\b(hoy|today|ahora|now)\b", t):
        return 0
    if re.search(r"\b(ayer|yesterday)\b", t):
        return 1
    m = re.search(rf"(\d+|un|una|a|an)\s+({UNIT_RE})", t)
    if not m:
        return None
    n = 1 if not m.group(1).isdigit() else int(m.group(1))
    return n * UNIT_DAYS[m.group(2)]


def parse_count(text):
    """'1.234 seguidores' -> 1234, '12K followers' -> 12000, '1,5 mil seguidores' -> 1500."""
    m = re.search(r"(\d[\d.,]*)\s*(k|m|mil)?\b", text.lower())
    if not m:
        return None
    num, suffix = m.group(1), m.group(2)
    if suffix:
        value = float(num.replace(",", "."))
        return int(value * (1_000_000 if suffix == "m" else 1_000))
    return int(re.sub(r"[.,]", "", num))


FOLLOWERS_RE = re.compile(r"(\d[\d.,]*\s*(?:k|m|mil)?)\s*(?:seguidores|followers)", re.I)


def parse_followers(page_text):
    m = FOLLOWERS_RE.search(page_text)
    return parse_count(m.group(1)) if m else None


def parse_mutuals(text):
    """Contactos en común a partir del texto de una tarjeta de búsqueda.

    'Ana y 12 contactos más en común' -> 13
    'Ana, Luis and 10 other mutual connections' -> 12
    'Ana es un contacto en común' / 'Ana is a mutual connection' -> 1
    """
    t = text.lower()
    if not re.search(r"mutual|en com[uú]n", t):
        return 0
    named = 2 if re.search(r",[^,\n]+\b(?:and|y)\b", t) else 1
    m = re.search(r"\b(?:and|y)\s+(\d[\d.,]*)", t)
    if m:
        return int(re.sub(r"[.,]", "", m.group(1))) + named
    m = re.search(r"(\d[\d.,]*)\s+(?:mutual|contactos?|otros?)", t)
    if m:
        return int(re.sub(r"[.,]", "", m.group(1)))
    return 2 if re.search(r"\b(?:and|y)\b", t) else 1
