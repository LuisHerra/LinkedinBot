"""Comprueba tus proxies y los ordena por cercanía a Madrid para elegir PROXY_PRIMARY y PROXY_SECONDARY.

Uso (en local, nunca en GitHub):
    python tools/check_proxies.py proxies.txt

proxies.txt: una URL por línea, http://usuario:contraseña@host:puerto. Está en .gitignore.
"""
import json
import math
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

MADRID = (40.4168, -3.7038)
GEO_URL = "http://ip-api.com/json/?fields=status,message,query,country,countryCode,city,lat,lon,isp,org,hosting,proxy"


def km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def check(url):
    host = urlsplit(url).hostname
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": url, "https": url}))
        with opener.open(GEO_URL, timeout=20) as r:
            geo = json.loads(r.read())
        if geo.get("status") != "success":
            return {"host": host, "error": geo.get("message", "sin datos")}
        return {**geo, "host": host, "km": round(km(MADRID, (geo["lat"], geo["lon"])))}
    except Exception as e:
        return {"host": host, "error": str(e)[:60]}


def main(path):
    urls = [l.strip() for l in open(path, encoding="utf-8") if l.strip() and not l.startswith("#")]
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(check, urls))

    ok = sorted((r for r in results if "error" not in r), key=lambda r: r["km"])
    print(f"{'#':>2}  {'km':>5}  {'país':4} {'ciudad':18} {'IP salida':16} {'ISP':28} avisos")
    for i, r in enumerate(ok, 1):
        flags = []
        if r.get("hosting"):
            flags.append("DATACENTER")
        if r.get("proxy"):
            flags.append("marcado como proxy/VPN")
        print(f"{i:>2}  {r['km']:>5}  {r['countryCode']:4} {r['city'][:18]:18} {r['query']:16} {r['isp'][:28]:28} {', '.join(flags)}")
    for r in results:
        if "error" in r:
            print(f" ✘  {r['host']}: {r['error']}")

    clean = [r for r in ok if not r.get("hosting")]
    if clean:
        best = clean[0]
        same = [r for r in clean[1:] if r["countryCode"] == best["countryCode"]]
        print(f"\nPrincipal recomendado: {best['host']} ({best['city']}, {best['country']}, {best['km']} km)")
        if same:
            print(f"Reserva recomendada:   {same[0]['host']} ({same[0]['city']}, mismo país)")
        else:
            print("Reserva: no hay otro proxy en el mismo país; usa el siguiente más cercano y deja proxy.mode en sticky.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "proxies.txt")
