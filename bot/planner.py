"""Planificación aleatoria de envíos.

Semana: objetivo aleatorio entre weekly_target_min y weekly_target_max (o el de la rampa de calentamiento),
repartido entre los días con pesos Gamma, que dan días flojos y días fuertes en vez de un reparto uniforme.
Día: la cuota del día se parte en 2-5 tandas en horas aleatorias dentro de la ventana.

El planificador solo propone cuántas mandar; el tope real lo impone el runner con el conteo móvil de 7 días.
"""
from datetime import timedelta


def week_key(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def split_quota(total, weights, cap):
    """Reparte `total` en enteros proporcionales a `weights`, sin pasar de `cap` en ninguno."""
    n = len(weights)
    if n == 0 or total <= 0:
        return [0] * n
    total = min(total, cap * n)
    alloc = [0] * n
    remaining = total
    open_idx = list(range(n))
    # Reparto proporcional iterativo: lo que excede el tope se redistribuye entre los demás.
    while remaining > 0 and open_idx:
        wsum = sum(weights[i] for i in open_idx) or len(open_idx)
        raw = {i: remaining * (weights[i] or 1) / wsum for i in open_idx}
        floors = {i: int(raw[i]) for i in open_idx}
        leftover = remaining - sum(floors.values())
        for i in sorted(open_idx, key=lambda i: raw[i] - floors[i], reverse=True)[:leftover]:
            floors[i] += 1
        remaining = 0
        for i in open_idx:
            give = min(floors[i], cap - alloc[i])
            alloc[i] += give
            remaining += floors[i] - give
        open_idx = [i for i in open_idx if alloc[i] < cap]
    return alloc


def random_composition(total, parts, rng):
    """Parte `total` en `parts` enteros >= 1 de tamaño aleatorio."""
    parts = max(1, min(parts, total))
    cuts = sorted(rng.sample(range(1, total), parts - 1)) if parts > 1 else []
    bounds = [0, *cuts, total]
    return [bounds[i + 1] - bounds[i] for i in range(parts)]


def weekly_target(cfg, weeks_active, rng):
    lim = cfg["limits"]
    warmup = lim.get("warmup_weekly_targets") or []
    if weeks_active < len(warmup):
        return warmup[weeks_active]
    return rng.randint(lim["weekly_target_min"], lim["weekly_target_max"])


def make_week_plan(today, cfg, weeks_active, rng):
    lim, sch = cfg["limits"], cfg["schedule"]
    monday = today - timedelta(days=today.weekday())
    days = [monday + timedelta(days=i) for i in range(7)]
    target = weekly_target(cfg, weeks_active, rng)

    rest = rng.randint(*sch["rest_days_per_week"])
    rest_days = set(rng.sample(range(7), rest))
    weights = [0.0 if i in rest_days else rng.gammavariate(2.0, 1.0) for i in range(7)]
    # Si el bot arranca a mitad de semana, los días pasados no cuentan y el objetivo se escala.
    remaining_idx = [i for i, d in enumerate(days) if d >= today and weights[i] > 0]
    if not remaining_idx:
        remaining_idx = [days.index(today)]
        weights[remaining_idx[0]] = 1.0
    target = round(target * len([i for i, d in enumerate(days) if d >= today]) / 7)
    quotas = split_quota(target, [weights[i] for i in remaining_idx], lim["daily_cap"])
    per_day = {d.isoformat(): 0 for d in days}
    for i, q in zip(remaining_idx, quotas):
        per_day[days[i].isoformat()] = q
    return {"week": week_key(today), "target": sum(per_day.values()), "days": per_day}


def make_day_plan(now_local, quota, cfg, rng):
    sch = cfg["schedule"]
    start = max(sch["window_start_hour"], now_local.hour)
    hours = list(range(start, sch["window_end_hour"]))
    batches = []
    if quota > 0 and hours:
        n = min(rng.randint(*sch["batches_per_day"]), quota, len(hours))
        chosen = sorted(rng.sample(hours, n))
        sizes = random_composition(quota, n, rng)
        batches = [{"hour": h, "quota": q, "done": False} for h, q in zip(chosen, sizes)]
    return {"date": now_local.date().isoformat(), "quota": quota, "batches": batches}


def ensure_plans(db, cfg, now_local, rng):
    today = now_local.date()
    week = db.get("week_plan")
    if not week or week["week"] != week_key(today):
        weeks_active = db.get("weeks_active", 0)
        week = make_week_plan(today, cfg, weeks_active, rng)
        db.set("week_plan", week)
        # Una semana empezada a partir del miércoles no cuenta como semana de calentamiento.
        if today.weekday() <= 1:
            db.set("weeks_active", weeks_active + 1)
        db.log("week_plan", f"{week['week']} objetivo={week['target']} {week['days']}")

    day = db.get("day_plan")
    if not day or day["date"] != today.isoformat():
        quota = week["days"].get(today.isoformat(), 0)
        day = make_day_plan(now_local, quota, cfg, rng)
        db.set("day_plan", day)
        db.log("day_plan", f"{day['date']} cuota={quota} tandas={[(b['hour'], b['quota']) for b in day['batches']]}")
    return week, day


def due_quota(day, now_local):
    """Suma de las tandas pendientes cuya hora ya ha llegado (agrupa las que se saltaron por retrasos del cron)."""
    return sum(b["quota"] for b in day["batches"] if not b["done"] and b["hour"] <= now_local.hour)


def mark_due_done(db, day, now_local):
    for b in day["batches"]:
        if b["hour"] <= now_local.hour:
            b["done"] = True
    db.set("day_plan", day)
