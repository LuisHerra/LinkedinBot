import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bot import planner, runner
from bot.config import load_config
from bot.db import DB
from bot.parsing import clean_profile_url, parse_count, parse_days_ago, parse_followers, parse_mutuals
from bot.scoring import eligible, score

CFG = load_config()
MADRID = ZoneInfo("Europe/Madrid")


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "state.db")
    yield d
    d.close()


# --- parsing ---
@pytest.mark.parametrize("text,expected", [
    ("Enviada hace 3 días", 3), ("Sent 2 weeks ago", 14), ("Enviada hace 1 mes", 30),
    ("Enviada hace 5 horas", 0), ("Enviada hoy", 0), ("Sent yesterday", 1), ("Sent a month ago", 30),
    ("Ingeniero de software", None),
])
def test_days_ago(text, expected):
    assert parse_days_ago(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Ana y 12 contactos más en común", 13),
    ("Ana, Luis y 10 contactos más en común", 12),
    ("Ana García, Luis Pérez and 10 other mutual connections", 12),
    ("Ana es un contacto en común", 1),
    ("Ana y Luis son contactos en común", 2),
    ("12 contactos en común", 12),
    ("Madrid, España", 0),
])
def test_mutuals(text, expected):
    assert parse_mutuals(text) == expected


def test_counts():
    assert parse_count("1.234") == 1234
    assert parse_count("12K") == 12000
    assert parse_count("1,5 mil") == 1500
    assert parse_followers("Actividad\n2.345 seguidores\nPublicaciones") == 2345
    assert parse_followers("12K followers") == 12000
    assert parse_followers("Sin datos") is None


def test_clean_url():
    assert clean_profile_url("/in/ana-garcia-123/?miniProfileUrn=x") == "https://www.linkedin.com/in/ana-garcia-123/"
    assert clean_profile_url("https://www.linkedin.com/company/acme/") is None


def test_search_card_2026_layout():
    from bot.linkedin import parse_search_card
    text = ("Zakaria L. \n • 2º\n\nAI IT & Voz Engineer en Intrum\n\nAlcalá de Henares, Comunidad de Madrid, España\n\n"
            "Conectar\n\nAnterior: Estudiante en Universidad de Alcalá y 3 cursos\n\n"
            "Sergio Caro Álvaro, Aaron Martinez y 8 contactos más en común")
    c = parse_search_card("https://www.linkedin.com/in/zakaria-l-123/?mini=1", text)
    assert c == {"profile_url": "https://www.linkedin.com/in/zakaria-l-123/", "name": "Zakaria L.",
                 "headline": "AI IT & Voz Engineer en Intrum", "mutuals": 10}
    one = parse_search_card("/in/oscar/", "Óscar \n • 2º\n\nIngeniero IA\n\nMaksudur Rahman Mredul es contacto en común")
    assert one["mutuals"] == 1


# --- scoring ---
def test_scoring_prefers_mutuals_and_few_followers():
    small = {"mutuals": 20, "followers": 400, "headline": ""}
    star = {"mutuals": 20, "followers": 50_000, "headline": ""}
    few = {"mutuals": 3, "followers": 400, "headline": ""}
    assert score(small, CFG) > score(star, CFG)
    assert score(small, CFG) > score(few, CFG)
    assert eligible(star, CFG) == (False, "demasiados seguidores")
    assert eligible({"mutuals": 1}, CFG)[0] is False
    assert eligible({"mutuals": 5, "creator": True}, CFG)[0] is False


def test_affinity_and_seniority():
    base = {"mutuals": 10, "followers": 800, "headline": "Estudiante de informática"}
    alumni = {**base, "profile_text": "Educación\nUniversidad de Alcalá\nGrado en Ingeniería Informática"}
    senior = {**base, "headline": "Director of Engineering"}
    assert score(alumni, CFG) > score(base, CFG) > score(senior, CFG)
    colleague = {**base, "profile_text": "Experiencia\nCayoTV\nProfesora en Ellas Hablan Código"}
    assert score(colleague, CFG) > score(base, CFG)


def test_job_and_known_bonus():
    base = {"mutuals": 4, "followers": 800, "headline": "Desarrollador de proyectos"}
    recruiter = {**base, "headline": "Head of Talent Acquisition | IT Recruiter"}
    cto = {**base, "headline": "CTO, startup de streaming"}
    director = {**base, "headline": "Director comercial"}
    assert score(recruiter, CFG) > score(base, CFG)
    assert score(cto, CFG) > score(base, CFG) > score(director, CFG)
    known = {**base, "mutuals": CFG["targeting"]["known_min_mutuals"]}
    almost = {**base, "mutuals": CFG["targeting"]["known_min_mutuals"] - 1}
    assert score(known, CFG) - score(almost, CFG) > CFG["scoring"]["w_known"]


def test_pick_search_uses_segments():
    rng = random.Random(0)
    names = {runner.pick_search(CFG, rng)[0]["name"] for _ in range(300)}
    assert names == {s["name"] for s in CFG["targeting"]["segments"] if s.get("weight", 1) > 0}
    for seg in CFG["targeting"]["segments"]:
        assert seg["keywords"]


def test_segment_min_mutuals_and_exclude():
    alumni = next(s for s in CFG["targeting"]["segments"] if s["name"] == "alumni")
    assert alumni["min_mutuals"] < CFG["targeting"]["min_mutuals"]
    assert eligible({"mutuals": 1}, CFG, alumni["min_mutuals"])[0] is True
    rng = random.Random(1)
    assert all(runner.pick_search(CFG, rng, exclude=("alumni",))[0]["name"] != "alumni" for _ in range(100))


# --- planner ---
def test_split_quota_respects_cap_and_total():
    for seed in range(200):
        rng = random.Random(seed)
        w = [rng.random() for _ in range(rng.randint(1, 7))]
        total = rng.randint(0, 120)
        out = planner.split_quota(total, w, 25)
        assert all(0 <= x <= 25 for x in out)
        assert sum(out) == min(total, 25 * len(w))


def test_week_plan_in_range_and_not_uniform():
    cfg = {**CFG, "limits": {**CFG["limits"], "warmup_weekly_targets": []}}
    monday = date(2026, 11, 2)
    shapes = set()
    for seed in range(100):
        plan = planner.make_week_plan(monday, cfg, weeks_active=5, rng=random.Random(seed))
        assert 90 <= plan["target"] <= 100
        assert all(q <= 25 for q in plan["days"].values())
        shapes.add(tuple(plan["days"].values()))
    assert len(shapes) > 90


def test_warmup_targets():
    monday = date(2026, 11, 2)
    assert planner.make_week_plan(monday, CFG, 0, random.Random(1))["target"] == 50
    assert planner.make_week_plan(monday, CFG, 1, random.Random(1))["target"] == 75


def test_midweek_start_scales_target():
    cfg = {**CFG, "limits": {**CFG["limits"], "warmup_weekly_targets": []}}
    friday = date(2026, 11, 6)
    plan = planner.make_week_plan(friday, cfg, 5, random.Random(3))
    past = [d for d in plan["days"] if d < friday.isoformat()]
    assert all(plan["days"][d] == 0 for d in past)
    assert plan["target"] <= 3 * 25


def test_day_plan_batches_inside_window():
    for seed in range(100):
        rng = random.Random(seed)
        now = datetime(2026, 11, 2, 7, 30, tzinfo=MADRID)
        quota = rng.randint(1, 25)
        day = planner.make_day_plan(now, quota, CFG, rng)
        assert sum(b["quota"] for b in day["batches"]) == quota
        assert all(9 <= b["hour"] < 20 for b in day["batches"])
        assert 2 <= len(day["batches"]) <= 5 or len(day["batches"]) == quota


def test_due_groups_missed_batches(db):
    day = {"date": "2026-11-02", "quota": 10, "batches": [
        {"hour": 10, "quota": 3, "done": False}, {"hour": 12, "quota": 4, "done": False},
        {"hour": 17, "quota": 3, "done": False}]}
    now = datetime(2026, 11, 2, 13, 5, tzinfo=MADRID)
    assert planner.due_quota(day, now) == 7
    planner.mark_due_done(db, day, now)
    assert planner.due_quota(db.get("day_plan"), now) == 0


# --- topes y manuales ---
def test_allowance_counts_manual_invites(db):
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(MADRID)
    for i in range(80):
        db.record_invite({"profile_url": f"https://www.linkedin.com/in/bot{i}/"},
                         sent_at=now_utc - timedelta(days=1 + i % 5))
    # 15 manuales detectadas en el gestor de enviadas, hace 2 días
    pending = [{"profile_url": f"https://www.linkedin.com/in/man{i}/", "name": None, "days_ago": 2} for i in range(15)]
    assert db.sync_pending(pending) == 15
    allowed, sent_7d, _ = runner.allowance(db, CFG, now_local, wanted=10)
    assert sent_7d == 95
    assert allowed == 5


def test_daily_cap(db):
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(MADRID)
    for i in range(24):
        db.record_invite({"profile_url": f"https://www.linkedin.com/in/hoy{i}/"}, sent_at=now_utc - timedelta(minutes=i))
    assert runner.allowance(db, CFG, now_local, wanted=6)[0] == 1


def test_sync_marks_resolved(db):
    db.record_invite({"profile_url": "https://www.linkedin.com/in/a/"})
    db.record_invite({"profile_url": "https://www.linkedin.com/in/b/"})
    db.sync_pending([{"profile_url": "https://www.linkedin.com/in/a/", "days_ago": 0}])
    assert db.stats()["resolved"] == 1 and db.stats()["pending"] == 1


def test_overdue_batches_are_capped(db):
    now = datetime(2026, 10, 31, 19, 10, tzinfo=MADRID)
    db.set("week_plan", {"week": planner.week_key(now.date()), "target": 10, "days": {now.date().isoformat(): 10}})
    db.set("day_plan", {"date": now.date().isoformat(), "quota": 10, "batches": [
        {"hour": 17, "quota": 5, "done": False}, {"hour": 18, "quota": 1, "done": False},
        {"hour": 19, "quota": 4, "done": False}]})
    due, why = runner.check_due(db, CFG, now, random.Random(0))
    assert due == CFG["schedule"]["max_per_batch"] and "recortada de 10" in why


def test_pause(db):
    assert not runner.is_paused(db)
    runner.pause(db, 48, "test")
    assert runner.is_paused(db)
