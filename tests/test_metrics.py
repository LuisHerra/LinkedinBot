import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest

from bot import metrics, server
from bot.config import load_config
from bot.db import DB

CFG = load_config()
MADRID = ZoneInfo("Europe/Madrid")


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "state.db")
    yield d
    d.close()


def seed(db, now_local):
    now_utc = now_local.astimezone(timezone.utc)
    db.record_invite({"profile_url": "https://www.linkedin.com/in/ana/", "name": "Ana", "mutuals": 8,
                      "followers": 400, "score": 4.2}, sent_at=now_utc - timedelta(hours=1))
    db.record_invite({"profile_url": "https://www.linkedin.com/in/luis/", "name": "Luis"}, source="manual",
                     sent_at=now_utc - timedelta(days=2))
    db.log("run", json.dumps({"wanted": 2, "sent": 1, "outcome": "ok", "dry_run": False}))
    db.set("day_plan", {"date": now_local.date().isoformat(), "quota": 5, "batches": [
        {"hour": 10, "quota": 2, "done": True}, {"hour": 17, "quota": 3, "done": False}]})
    db.set("week_plan", {"week": "2026-W45", "target": 95,
                         "days": {(now_local.date() + timedelta(days=i)).isoformat(): 10 for i in range(-1, 6)}})


def test_next_operation_upcoming_batch(db):
    now = datetime(2026, 11, 4, 12, 30, tzinfo=MADRID)
    seed(db, now)
    nxt = metrics.next_operation(db, CFG, now)
    assert nxt["type"] == "batch" and nxt["quota"] == 3
    assert nxt["earliest"].startswith("2026-11-04T17:05") and nxt["latest"].startswith("2026-11-04T17:30")


def test_next_operation_tomorrow_and_paused(db):
    now = datetime(2026, 11, 4, 18, 0, tzinfo=MADRID)
    seed(db, now)
    plan = db.get("day_plan")
    plan["batches"][1]["done"] = True
    db.set("day_plan", plan)
    nxt = metrics.next_operation(db, CFG, now)
    assert nxt["type"] == "day" and nxt["earliest"].startswith("2026-11-05T09:05")
    db.set("paused_until", (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat())
    assert metrics.next_operation(db, CFG, now)["type"] == "paused"


def test_summary_has_no_personal_data(db):
    now = datetime.now(MADRID)
    seed(db, now)
    summary = metrics.build(db, CFG, now, detailed=False)
    text = json.dumps(summary)
    assert "Ana" not in text and "linkedin.com/in" not in text
    assert summary["week"]["rolling_7d"] == 2
    assert summary["last_run"]["outcome"] == "ok"
    detailed = metrics.build(db, CFG, now, detailed=True)
    assert [i["name"] for i in detailed["invitations_7d"]] == ["Ana", "Luis"]


def test_server_auth(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    d = DB(path)
    seed(d, datetime.now(MADRID))
    d.close()
    monkeypatch.setenv("BOT_DB", str(path))
    monkeypatch.setenv("METRICS_TOKEN", "secreto")
    monkeypatch.setenv("METRICS_PUBLIC", "1")
    server.Handler.cfg = CFG
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        summary = json.load(urllib.request.urlopen(f"{base}/api/summary"))
        assert "invitations_today" not in summary
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}/api/metrics")
        assert e.value.code == 401
        req = urllib.request.Request(f"{base}/api/metrics", headers={"Authorization": "Bearer secreto"})
        assert json.load(urllib.request.urlopen(req))["invitations_today"][0]["name"] == "Ana"
    finally:
        httpd.shutdown()
