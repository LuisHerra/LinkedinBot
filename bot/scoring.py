import math


def contains_any(text, terms):
    text = (text or "").lower()
    return any(term.lower() in text for term in terms or [])


def eligible(c, cfg):
    t = cfg["targeting"]
    if (c.get("mutuals") or 0) < t["min_mutuals"]:
        return False, "pocos contactos en común"
    if c.get("followers") is not None and c["followers"] > t["max_followers"]:
        return False, "demasiados seguidores"
    if t.get("skip_creator_mode") and c.get("creator"):
        return False, "modo creador"
    if c.get("can_connect") is False:
        return False, "sin botón de conectar"
    return True, ""


def score(c, cfg):
    w, t = cfg["scoring"], cfg["targeting"]
    s = w["w_mutuals"] * math.log1p(c.get("mutuals") or 0)
    # Sin dato de seguidores se asume un perfil medio (500) en vez de premiarlo.
    s -= w["w_followers"] * math.log10(max(c.get("followers") or 500, 1))
    # El espacio final permite términos como "ia " sin que coincidan dentro de otras palabras al final del titular.
    headline = (c.get("headline") or "") + " "
    if contains_any(headline, t.get("headline_boost_terms")):
        s += w["w_headline"]
    if contains_any(headline, t.get("senior_terms")):
        s -= w.get("w_senior", 0)
    if contains_any(c.get("profile_text"), t.get("affinity_terms")):
        s += w.get("w_affinity", 0)
    return round(s, 3)
