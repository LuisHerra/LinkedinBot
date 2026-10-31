import json
import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS invitations (
    profile_url TEXT PRIMARY KEY,
    name TEXT,
    headline TEXT,
    sent_at TEXT NOT NULL,          -- ISO UTC (estimado para las manuales)
    source TEXT NOT NULL,           -- 'bot' | 'manual'
    status TEXT NOT NULL,           -- 'pending' | 'resolved' | 'withdrawn'
    mutuals INTEGER,
    followers INTEGER,
    score REAL,
    last_seen_pending TEXT
);
CREATE TABLE IF NOT EXISTS skipped (
    profile_url TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
"""


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.commit()
        self.conn.close()

    # --- kv ---
    def get(self, key, default=None):
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key, value):
        self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        self.conn.commit()

    def log(self, kind, detail=""):
        self.conn.execute("INSERT INTO events VALUES (?, ?, ?)", (iso(utcnow()), kind, detail))
        self.conn.commit()

    # --- invitaciones ---
    def known_urls(self, skip_days=60):
        cutoff = iso(utcnow() - timedelta(days=skip_days))
        urls = {r[0] for r in self.conn.execute("SELECT profile_url FROM invitations")}
        urls |= {r[0] for r in self.conn.execute("SELECT profile_url FROM skipped WHERE seen_at >= ?", (cutoff,))}
        return urls

    def record_invite(self, cand, source="bot", sent_at=None):
        now = iso(sent_at or utcnow())
        self.conn.execute(
            """INSERT INTO invitations (profile_url, name, headline, sent_at, source, status, mutuals, followers, score, last_seen_pending)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
               ON CONFLICT(profile_url) DO NOTHING""",
            (cand["profile_url"], cand.get("name"), cand.get("headline"), now, source,
             cand.get("mutuals"), cand.get("followers"), cand.get("score"), now),
        )
        self.conn.commit()

    def record_skip(self, url, reason):
        self.conn.execute(
            "INSERT INTO skipped VALUES (?, ?, ?) ON CONFLICT(profile_url) DO UPDATE SET seen_at = excluded.seen_at, reason = excluded.reason",
            (url, iso(utcnow()), reason),
        )
        self.conn.commit()

    def sync_pending(self, pending, now=None):
        """Cruza la lista de invitaciones pendientes de LinkedIn con la base de datos.

        Las que no conocemos son invitaciones que mandaste tú a mano: se guardan como 'manual'
        con la fecha estimada a partir de "hace X días", para que cuenten en el tope semanal.
        Las nuestras que ya no aparecen se marcan como 'resolved' (aceptadas o rechazadas).
        """
        now = now or utcnow()
        seen = set()
        new_manual = 0
        for item in pending:
            url = item["profile_url"]
            seen.add(url)
            row = self.conn.execute("SELECT status FROM invitations WHERE profile_url = ?", (url,)).fetchone()
            if row is None:
                sent_at = now - timedelta(days=item["days_ago"])
                self.record_invite({"profile_url": url, "name": item.get("name")}, source="manual", sent_at=sent_at)
                new_manual += 1
            self.conn.execute(
                "UPDATE invitations SET last_seen_pending = ?, status = 'pending' WHERE profile_url = ? AND status != 'withdrawn'",
                (iso(now), url),
            )
        for row in self.conn.execute("SELECT profile_url FROM invitations WHERE status = 'pending'").fetchall():
            if row["profile_url"] not in seen:
                self.conn.execute("UPDATE invitations SET status = 'resolved' WHERE profile_url = ?", (row["profile_url"],))
        self.conn.commit()
        return new_manual

    def mark_withdrawn(self, url):
        self.conn.execute("UPDATE invitations SET status = 'withdrawn' WHERE profile_url = ?", (url,))
        self.conn.commit()

    def sent_since(self, since):
        row = self.conn.execute("SELECT COUNT(*) FROM invitations WHERE sent_at >= ?", (iso(since),)).fetchone()
        return row[0]

    def stats(self):
        q = self.conn.execute
        return {
            "total_bot": q("SELECT COUNT(*) FROM invitations WHERE source = 'bot'").fetchone()[0],
            "total_manual": q("SELECT COUNT(*) FROM invitations WHERE source = 'manual'").fetchone()[0],
            "pending": q("SELECT COUNT(*) FROM invitations WHERE status = 'pending'").fetchone()[0],
            "resolved": q("SELECT COUNT(*) FROM invitations WHERE status = 'resolved'").fetchone()[0],
            "withdrawn": q("SELECT COUNT(*) FROM invitations WHERE status = 'withdrawn'").fetchone()[0],
            "last_7d": self.sent_since(utcnow() - timedelta(days=7)),
        }
