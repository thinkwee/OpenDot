"""SQLite store. Small, synchronous, WAL — fast enough for a personal agent."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, name TEXT UNIQUE, emoji TEXT, color TEXT, role TEXT,
  tagline TEXT, status TEXT DEFAULT 'idle', status_text TEXT DEFAULT '',
  created REAL
);
CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY, title TEXT, kind TEXT, members TEXT, created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, thread_id TEXT, role TEXT, agent_id TEXT, content TEXT,
  meta TEXT DEFAULT '{}', created REAL
);
CREATE INDEX IF NOT EXISTS ix_msg_thread ON messages(thread_id, created);
CREATE TABLE IF NOT EXISTS steps (
  id TEXT PRIMARY KEY, thread_id TEXT, run_id TEXT, agent_id TEXT, tool TEXT, args TEXT,
  result TEXT, status TEXT, created REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS ix_steps_thread ON steps(thread_id, created);
CREATE TABLE IF NOT EXISTS automations (
  id TEXT PRIMARY KEY, agent_id TEXT, name TEXT, kind TEXT, schedule TEXT, event_filter TEXT,
  prompt TEXT, enabled INTEGER DEFAULT 1, last_run REAL, next_run REAL, thread_id TEXT,
  created REAL
);
CREATE TABLE IF NOT EXISTS inbox (
  id TEXT PRIMARY KEY, agent_id TEXT, kind TEXT, title TEXT, body TEXT, status TEXT,
  ref TEXT, thread_id TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, agent_id TEXT, thread_id TEXT, tool TEXT, args TEXT, reason TEXT,
  status TEXT, created REAL, decided REAL
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, source TEXT, type TEXT, payload TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
-- durable work (see durable.py): every agent run is a job, saved before it starts
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, thread_id TEXT, agent_id TEXT, kind TEXT, source TEXT, prompt TEXT,
  hops INTEGER DEFAULT 0, from_id TEXT, run_id TEXT, status TEXT, attempts INTEGER DEFAULT 0,
  created REAL, started REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
-- outside-world tool calls, written before they run so a resumed job never repeats one
CREATE TABLE IF NOT EXISTS effects (
  id TEXT PRIMARY KEY, job_id TEXT, thread_id TEXT, tool TEXT, args_hash TEXT,
  attempt INTEGER, result TEXT, created REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS ix_effects_key ON effects(job_id, tool, args_hash);
"""

JSON_COLS = {"members", "meta", "args", "payload"}

# columns added after v0.2 — existing databases get them via ALTER TABLE on open
MIGRATIONS = {
    "agents": {
        "responsibility": "TEXT DEFAULT ''",  # the one thing this agent takes care of
        "context": "TEXT DEFAULT ''",         # what it needs to know to do it
        "boundary": "TEXT DEFAULT ''",        # when to come back to the human
        "origin": "TEXT DEFAULT 'manual'",    # manual | chat | link | qr | template | default
        "origin_url": "TEXT DEFAULT ''",
        "avatar": "TEXT DEFAULT ''",          # animal key (fox, cat, …); '' = pick by id
    },
    "automations": {
        "every_min": "INTEGER",               # watch: minutes between checks
        "expires": "REAL",                    # watch: give up after this time
        "outcome": "TEXT DEFAULT ''",         # watch: what happened when it closed
        "checks": "INTEGER DEFAULT 0",
    },
    "approvals": {
        "job_id": "TEXT",                     # the job that asked; a resumed job reuses it
        "args_hash": "TEXT",
        "attempt": "INTEGER DEFAULT 0",
    },
}


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


class DB:
    def __init__(self) -> None:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = settings.DATA_DIR / "dot.db"
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.lock = threading.Lock()

    def _migrate(self) -> None:
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self.conn.commit()

    def ensure_schema(self, sql: str) -> None:
        """Extensions add their own tables with CREATE TABLE IF NOT EXISTS."""
        with self.lock:
            self.conn.executescript(sql)
            self.conn.commit()

    def _row(self, r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in JSON_COLS & d.keys():
            try:
                d[k] = json.loads(d[k]) if d[k] else ({} if k != "members" else [])
            except (TypeError, ValueError):
                pass
        return d

    def q(self, sql: str, *args: Any) -> list[dict]:
        with self.lock:
            return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, *args: Any) -> dict | None:
        with self.lock:
            return self._row(self.conn.execute(sql, args).fetchone())

    @staticmethod
    def _prep(table: str, row: dict) -> tuple[str, tuple]:
        row.setdefault("id", new_id())
        if "created" not in row and table != "kv":
            row["created"] = time.time()
        for k in JSON_COLS & row.keys():
            if not isinstance(row[k], str):
                row[k] = json.dumps(row[k], ensure_ascii=False, default=str)
        cols = ",".join(row)
        return (f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({','.join('?' * len(row))})",
                tuple(row.values()))

    def insert(self, table: str, **row: Any) -> dict:
        return self.insert_many((table, row))[0]

    def insert_many(self, *rows: tuple[str, dict]) -> list[dict]:
        """Insert several rows in one transaction — all saved, or none."""
        stmts = [self._prep(table, row) for table, row in rows]
        with self.lock:
            with self.conn:  # commits on success, rolls back on error
                for sql, vals in stmts:
                    self.conn.execute(sql, vals)
        return [self.one(f"SELECT * FROM {t} WHERE id=?", r["id"]) or r for t, r in rows]

    def update(self, table: str, id_: str, **fields: Any) -> None:
        if not fields:
            return
        for k in JSON_COLS & fields.keys():
            if not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k], ensure_ascii=False, default=str)
        sets = ",".join(f"{k}=?" for k in fields)
        with self.lock:
            self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), id_))
            self.conn.commit()

    def delete(self, table: str, id_: str) -> None:
        with self.lock:
            self.conn.execute(f"DELETE FROM {table} WHERE id=?", (id_,))
            self.conn.commit()

    def kv_get(self, k: str, default: Any = None) -> Any:
        r = self.one("SELECT v FROM kv WHERE k=?", k)
        return json.loads(r["v"]) if r else default

    def kv_set(self, k: str, v: Any) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", (k, json.dumps(v)))
            self.conn.commit()


db = DB()
