"""CLI.

  python -m bot due              ¿toca tanda esta hora? (sin navegador; escribe due=true/false en GITHUB_OUTPUT)
  python -m bot run [--dry-run] [--force]
  python -m bot plan             muestra el plan de la semana y del día
  python -m bot stats
  python -m bot unpause          quita la pausa (tras renovar la cookie o resolver una verificación)
"""
import argparse
import json
import os
import random
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from . import planner, runner
from .config import DEFAULT_DB, load_config
from .db import DB


def github_output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def main():
    ap = argparse.ArgumentParser(prog="bot")
    ap.add_argument("command", choices=["due", "run", "plan", "stats", "unpause"])
    ap.add_argument("--dry-run", action="store_true", help="Todo menos pulsar el botón de enviar")
    ap.add_argument("--force", action="store_true", help="Ejecuta una tanda aunque no toque (respeta los topes)")
    ap.add_argument("--quota", type=int, default=5, help="Tamaño de la tanda con --force")
    args = ap.parse_args()

    cfg = load_config()
    db = DB(DEFAULT_DB)
    rng = random.SystemRandom()
    now_local = datetime.now(ZoneInfo(cfg["timezone"]))
    try:
        if args.command == "stats":
            print(json.dumps(db.stats(), indent=2, ensure_ascii=False))
            return 0

        if args.command == "unpause":
            db.set("paused_until", None)
            db.log("unpaused")
            print("Pausa eliminada")
            return 0

        if args.command == "plan":
            planner.ensure_plans(db, cfg, now_local, rng)
            print(json.dumps({"week": db.get("week_plan"), "day": db.get("day_plan"),
                              "paused_until": db.get("paused_until")}, indent=2, ensure_ascii=False))
            return 0

        due, why = runner.check_due(db, cfg, now_local, rng)
        print(f"[{now_local:%Y-%m-%d %H:%M}] cuota pendiente esta hora: {due} — {why}")
        if args.command == "due":
            github_output(due=str(due > 0 or args.force).lower())
            return 0

        wanted = args.quota if args.force else due
        if wanted <= 0:
            return 0
        return run(db, cfg, rng, now_local, wanted, args.dry_run)
    finally:
        db.close()


def run(db, cfg, rng, now_local, wanted, dry_run):
    from .browser import choose_proxy, open_page
    from .linkedin import Checkpoint, LinkedIn, NotLoggedIn

    day = db.get("day_plan")
    try:
        name, proxy_url, ip = choose_proxy(cfg, planner.week_key(now_local.date()))
        print(f"Proxy: {name} (IP de salida {ip})")
        if db.get("last_proxy_ip") not in (None, ip):
            db.log("proxy_ip_changed", f"{db.get('last_proxy_ip')} -> {ip}")
        db.set("last_proxy_ip", ip)

        with open_page(cfg, proxy_url) as page:
            li = LinkedIn(page, cfg, rng)
            try:
                sent = runner.run_batch(li, db, cfg, rng, now_local, wanted, dry_run)
            except (Checkpoint, NotLoggedIn):
                raise
            except Exception:
                # Captura de lo que mostraba LinkedIn al fallar; se sube como artefacto 'screenshots'.
                li.screenshot("error")
                print(f"URL al fallar: {page.url}")
                raise
        print(f"Tanda terminada: {sent} enviadas")
        return 0
    except Checkpoint as e:
        runner.pause(db, cfg["maintenance"]["pause_hours_on_checkpoint"], f"checkpoint {e}")
        print("::error::LinkedIn pide verificación. Entra a mano desde tu navegador y resuélvela; el bot queda en pausa.")
        return 2
    except NotLoggedIn:
        runner.pause(db, 24 * 365, "cookie li_at caducada")
        print("::error::LinkedIn ha invalidado la sesión. Vuelve a entrar en el Chrome del proxy, actualiza "
              "LI_COOKIES (o LI_AT) y lanza el workflow con la opción unpause.")
        return 3
    finally:
        # Se marca la tanda como hecha pase lo que pase: ante un fallo no se reintenta cada hora en bucle.
        if day and not dry_run:
            planner.mark_due_done(db, day, now_local)


if __name__ == "__main__":
    sys.exit(main())
