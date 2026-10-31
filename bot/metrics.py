"""Métricas del bot para la API (/api/summary y /api/metrics).

summary: solo números, planes y horarios, sin datos de personas; se puede consumir desde el navegador.
metrics: añade a quién se envió y el registro de operaciones; requiere token.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .db import utcnow

# Retraso aleatorio máximo del temporizador de systemd (deploy/linkedin-bot.timer).
TIMER_MINUTE, TIMER_JITTER_MIN = 5, 25


def _local(iso_ts, tz):
    return datetime.fromisoformat(iso_ts).astimezone(tz).isoformat(timespec="seconds") if iso_ts else None


def next_operation(db, cfg, now_local):
    """Ventana estimada de la próxima tanda según el plan (el minuto exacto lo decide el temporizador)."""
    tz = now_local.tzinfo
    paused = db.get("paused_until")
    if paused and datetime.fromisoformat(paused) > utcnow():
        return {"type": "paused", "until": _local(paused, tz),
                "description": "En pausa: no se enviará nada hasta que termine la pausa o se quite a mano."}

    sch = cfg["schedule"]
    today = now_local.date().isoformat()
    day = db.get("day_plan")
    if day and day["date"] == today:
        pending = sorted((b for b in day["batches"] if not b["done"]), key=lambda b: b["hour"])
        if pending:
            b = pending[0]
            if b["hour"] <= now_local.hour:
                return {"type": "batch", "quota": b["quota"], "earliest": None, "latest": None,
                        "description": f"Tanda de {b['quota']} pendiente: se ejecuta en la próxima comprobación."}
            start = now_local.replace(hour=b["hour"], minute=TIMER_MINUTE, second=0, microsecond=0)
            return {"type": "batch", "quota": b["quota"], "earliest": start.isoformat(timespec="seconds"),
                    "latest": (start + timedelta(minutes=TIMER_JITTER_MIN)).isoformat(timespec="seconds"),
                    "description": f"Tanda de {b['quota']} invitaciones entre las {b['hour']}:{TIMER_MINUTE:02d} "
                                   f"y las {b['hour']}:{TIMER_MINUTE + TIMER_JITTER_MIN:02d}."}

    # Nada más hoy: el plan del próximo día con cuota se genera en su primera comprobación.
    week = db.get("week_plan") or {"days": {}}
    for offset in range(1, 8):
        d = now_local.date() + timedelta(days=offset)
        quota = week["days"].get(d.isoformat())
        if quota is None:  # semana nueva: aún no hay plan
            start = datetime(d.year, d.month, d.day, sch["window_start_hour"], TIMER_MINUTE, tzinfo=tz)
            return {"type": "new_week", "quota": None, "earliest": start.isoformat(timespec="seconds"),
                    "latest": None, "description": f"El {d.isoformat()} se genera el plan de la semana nueva."}
        if quota > 0:
            start = datetime(d.year, d.month, d.day, sch["window_start_hour"], TIMER_MINUTE, tzinfo=tz)
            end = datetime(d.year, d.month, d.day, sch["window_end_hour"] - 1, TIMER_MINUTE + TIMER_JITTER_MIN, tzinfo=tz)
            return {"type": "day", "quota": quota, "earliest": start.isoformat(timespec="seconds"),
                    "latest": end.isoformat(timespec="seconds"),
                    "description": f"El {d.isoformat()} tocan {quota} invitaciones en horas aleatorias."}
    return {"type": "none", "description": "No quedan envíos planificados esta semana."}


def _operations(db, since, tz, detailed):
    ops = []
    for e in db.events_since(since):
        op = {"at": _local(e["ts"], tz), "kind": e["kind"]}
        if e["kind"] == "run":
            try:
                data = json.loads(e["detail"])
            except (TypeError, ValueError):
                data = {}
            op.update({k: data.get(k) for k in ("wanted", "sent", "outcome", "dry_run")})
        elif detailed:
            op["detail"] = e["detail"]
        ops.append(op)
    return ops


def build(db, cfg, now_local=None, detailed=False):
    tz = ZoneInfo(cfg["timezone"])
    now_local = now_local or datetime.now(tz)
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    lim = cfg["limits"]

    today_inv = db.invitations_since(midnight)
    last7 = db.invitations_since(utcnow() - timedelta(days=7))
    runs = db.events_since(utcnow() - timedelta(days=30), kinds={"run"})
    last_run = None
    if runs:
        r = runs[0]
        data = json.loads(r["detail"]) if r["detail"] else {}
        last_run = {"at": _local(r["ts"], tz), **{k: data.get(k) for k in ("sent", "outcome", "dry_run")}}

    history = {}
    for inv in db.invitations_since(utcnow() - timedelta(days=30)):
        d = _local(inv["sent_at"], tz)[:10]
        h = history.setdefault(d, {"date": d, "bot": 0, "manual": 0})
        h[inv["source"]] = h.get(inv["source"], 0) + 1

    stats = db.stats()
    decided = stats["resolved"] + stats["pending"]
    out = {
        "generated_at": now_local.isoformat(timespec="seconds"),
        "status": "paused" if next_operation(db, cfg, now_local)["type"] == "paused" else "active",
        "paused_until": _local(db.get("paused_until"), tz),
        "last_check": _local(db.get("last_check"), tz),
        "last_run": last_run,
        "today": {
            "date": midnight.date().isoformat(),
            "sent_bot": sum(1 for i in today_inv if i["source"] == "bot"),
            "sent_manual": sum(1 for i in today_inv if i["source"] == "manual"),
            "daily_cap": lim["daily_cap"],
            "plan": db.get("day_plan") if (db.get("day_plan") or {}).get("date") == midnight.date().isoformat() else None,
        },
        "week": {
            "rolling_7d": len(last7),
            "hard_cap": lim["weekly_hard_cap"],
            "plan": db.get("week_plan"),
        },
        "totals": {**stats, "resolved_ratio": round(stats["resolved"] / decided, 3) if decided else None},
        "next_operation": next_operation(db, cfg, now_local),
        "history_30d": sorted(history.values(), key=lambda h: h["date"]),
        "operations_today": _operations(db, midnight, tz, detailed),
    }
    if detailed:
        out["invitations_today"] = [
            {k: i[k] for k in ("name", "headline", "profile_url", "source", "status", "mutuals", "followers", "score")}
            | {"sent_at": _local(i["sent_at"], tz)}
            for i in today_inv
        ]
        out["invitations_7d"] = [
            {k: i[k] for k in ("name", "profile_url", "source", "status", "mutuals", "followers", "score")}
            | {"sent_at": _local(i["sent_at"], tz)}
            for i in last7
        ]
    return out
