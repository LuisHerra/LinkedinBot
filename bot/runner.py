import math
from datetime import datetime, timedelta

from . import planner
from .db import iso, utcnow
from .scoring import eligible, score


def is_paused(db):
    until = db.get("paused_until")
    return until and datetime.fromisoformat(until) > utcnow()


def pause(db, hours, reason):
    db.set("paused_until", iso(utcnow() + timedelta(hours=hours)))
    db.log("paused", f"{hours}h: {reason}")


def allowance(db, cfg, now_local, wanted):
    """Cuántas se pueden enviar ya mismo: lo que pide la tanda, recortado por el tope móvil de 7 días y el diario.

    Cuenta todo lo registrado: las del bot y las manuales detectadas en el gestor de enviadas.
    """
    lim = cfg["limits"]
    sent_7d = db.sent_since(utcnow() - timedelta(days=7))
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = db.sent_since(midnight)
    return max(0, min(wanted, lim["weekly_hard_cap"] - sent_7d, lim["daily_cap"] - sent_today)), sent_7d, sent_today


def check_due(db, cfg, now_local, rng):
    """Paso barato (sin navegador) que decide si esta hora toca tanda."""
    if is_paused(db):
        return 0, f"en pausa hasta {db.get('paused_until')}"
    sch = cfg["schedule"]
    if not sch["window_start_hour"] <= now_local.hour < sch["window_end_hour"]:
        return 0, "fuera de la ventana horaria"
    _, day = planner.ensure_plans(db, cfg, now_local, rng)
    due = planner.due_quota(day, now_local)
    return due, f"plan de hoy: {[(b['hour'], b['quota'], b['done']) for b in day['batches']]}"


def maintenance(li, db, cfg, now_local):
    """Sincroniza las pendientes (detecta tus invitaciones manuales) y retira las antiguas una vez al día."""
    pending = li.pending_invitations()
    new_manual = db.sync_pending(pending)
    if new_manual:
        db.log("manual_detected", str(new_manual))
    m = cfg["maintenance"]
    if db.get("last_withdraw_date") != now_local.date().isoformat():
        old = [p for p in pending if p["days_ago"] >= m["withdraw_after_days"]]
        old.sort(key=lambda p: p["days_ago"], reverse=True)
        for p in old[: m["max_withdrawals_per_run"]]:
            if li.withdraw(p["profile_url"]):
                db.mark_withdrawn(p["profile_url"])
                db.log("withdrawn", p["profile_url"])
        db.set("last_withdraw_date", now_local.date().isoformat())
    return len(pending), new_manual


def pick_search(cfg, rng, only=None, exclude=()):
    """Elige segmento por peso (o el indicado en `only`) y una palabra clave dentro de él."""
    segments = cfg["targeting"]["segments"]
    if only:
        segments = [s for s in segments if s["name"] == only] or segments
    segments = [s for s in segments if s["name"] not in exclude] or segments
    seg = rng.choices(segments, weights=[s.get("weight", 1) for s in segments])[0]
    return seg, rng.choice(seg["keywords"])


def collect_candidates(li, db, cfg, rng, needed, only_segment=None, exclude=()):
    t = cfg["targeting"]
    known = db.known_urls()
    seg, keyword = pick_search(cfg, rng, only_segment, exclude)
    segment, geo = seg["name"], seg.get("geo_urns") or ()
    # Segmentos con afinidad propia (alumni, empresa) aceptan bien con menos contactos en común.
    min_mutuals = seg.get("min_mutuals", t["min_mutuals"])
    pages = rng.randint(*t["search_pages"])
    found, raw = 0, []
    for page_num in range(1, pages + 1):
        results = li.search(keyword, page_num, geo)
        found += len(results)
        raw += [c for c in results if c["profile_url"] not in known]
        li.pause()
    print(f"  '{segment}/{keyword}': {pages} pág., {found} perfiles leídos, {len(raw)} nuevos, "
          f"comunes={sorted((c['mutuals'] for c in raw), reverse=True)[:15]}")
    raw = [c for c in raw if c["mutuals"] >= min_mutuals]
    print(f"  con >= {min_mutuals} contactos en común: {len(raw)}")
    raw.sort(key=lambda c: c["mutuals"], reverse=True)

    to_inspect = raw[: math.ceil(needed * t["profiles_to_inspect_per_invite"])]
    ranked = []
    for c in to_inspect:
        c.update(li.inspect_profile(c["profile_url"]))
        ok, reason = eligible(c, cfg, min_mutuals)
        if ok:
            c["score"] = score(c, cfg)
            ranked.append(c)
        else:
            print(f"  descartado {c['name']}: {reason} (seg={c.get('followers')}, creador={c.get('creator')})")
            db.record_skip(c["profile_url"], reason)
        li.pause()
    ranked.sort(key=lambda c: c["score"], reverse=True)
    return segment, f"{segment}/{keyword}", ranked


def run_batch(li, db, cfg, rng, now_local, wanted, dry_run=False, only_segment=None):
    from .linkedin import WeeklyLimitReached

    li.ensure_logged_in()
    n_pending, new_manual = maintenance(li, db, cfg, now_local)
    allowed, sent_7d, sent_today = allowance(db, cfg, now_local, wanted)
    print(f"Pendientes: {n_pending} (nuevas manuales: {new_manual}) | 7d: {sent_7d} | hoy: {sent_today} | "
          f"tanda pedía {wanted}, permitido {allowed}")
    if allowed <= 0:
        return 0

    segment, search, ranked = collect_candidates(li, db, cfg, rng, allowed, only_segment)
    print(f"Búsqueda '{search}': {len(ranked)} candidatos elegibles")
    if len(ranked) < allowed and not only_segment:
        # Una sola búsqueda extra con otro segmento para no desperdiciar la cuota de la tanda.
        li.pause()
        _, search2, more = collect_candidates(li, db, cfg, rng, allowed - len(ranked), exclude=(segment,))
        print(f"Búsqueda extra '{search2}': {len(more)} candidatos elegibles")
        seen = {c["profile_url"] for c in ranked}
        ranked = sorted(ranked + [c for c in more if c["profile_url"] not in seen], key=lambda c: c["score"], reverse=True)
        search = f"{search} + {search2}"
    sent = 0
    for c in ranked[:allowed]:
        try:
            ok, reason = li.send_invite(c["profile_url"], dry_run=dry_run)
        except WeeklyLimitReached:
            pause(db, 72, "LinkedIn avisó del límite semanal")
            break
        print(f"  {'✔' if ok else '✘'} {c['name']} | comunes={c['mutuals']} seg={c.get('followers')} "
              f"score={c['score']} | {reason}")
        if ok and not dry_run:
            db.record_invite(c)
            sent += 1
        elif not ok:
            db.record_skip(c["profile_url"], reason)
        li.pause("delay_between_invites_s")
    db.log("batch", f"busqueda={search} enviadas={sent} dry_run={dry_run}")
    return sent
