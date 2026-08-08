"""Optional SQLite persistence for the three pieces of in-memory state.

Off unless STATE_DB is set, and when it is off nothing in this file runs — the
pool, the limiter and the explore pools keep their plain dicts. That is
deliberate: the zero-config local run should not grow a database file.

What it buys when it is on:

- **Cooldowns survive a restart.** This is the one that actually costs money.
  A spent daily free allowance is remembered for hours, and a process that
  restarts (Render's free plan sleeps) would otherwise retry the exhausted
  provider on the very next request, every time.
- **Rate limits survive a restart**, so a restart is not a way to reset them.
- **Explore questions are not repeated**, because the served set outlives the
  process that served them.

Deliberately stdlib-only. Adding redis for this would mean a dependency and a
server for what is a few kilobytes of state on one instance.

Concurrency: one connection per thread, WAL, autocommit. This is a
single-instance store — two processes pointed at the same file will not
corrupt it, but the rate limits become approximate under the write lock.
"""

import os
import sqlite3
import threading
from typing import Dict, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS cooldowns (
    key   TEXT PRIMARY KEY,
    until REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS hits (
    bucket TEXT NOT NULL,
    client TEXT NOT NULL,
    at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS hits_lookup ON hits (bucket, client, at);

CREATE TABLE IF NOT EXISTS questions (
    slug     TEXT NOT NULL,
    key      TEXT NOT NULL,
    question TEXT NOT NULL,
    hook     TEXT NOT NULL DEFAULT '',
    served   INTEGER NOT NULL DEFAULT 0,
    seq      INTEGER,
    PRIMARY KEY (slug, key)
);
CREATE INDEX IF NOT EXISTS questions_waiting ON questions (slug, served, seq);
"""


class StateStore:
    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # isolation_level=None is autocommit: every statement here is a
            # single small write, and a held-open transaction would block the
            # other threads for no benefit.
            conn = sqlite3.connect(self.path, isolation_level=None,
                                   check_same_thread=False, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    # ── cooldowns ────────────────────────────────────────────────────────

    def set_cooldown(self, key: str, until: float) -> None:
        self._conn().execute(
            "INSERT INTO cooldowns (key, until) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET until=excluded.until",
            (key, until),
        )

    def cooldowns(self, now: float) -> Dict[str, float]:
        """Only the live ones. Expired rows are dropped as they are noticed,
        which keeps the table from growing without a sweeper."""
        conn = self._conn()
        conn.execute("DELETE FROM cooldowns WHERE until <= ?", (now,))
        return {key: until for key, until in
                conn.execute("SELECT key, until FROM cooldowns")}

    # ── rate limiting ────────────────────────────────────────────────────

    def record_hit(self, bucket: str, client: str, at: float) -> None:
        self._conn().execute(
            "INSERT INTO hits (bucket, client, at) VALUES (?, ?, ?)",
            (bucket, client, at))

    def hits(self, bucket: str, client: str, cutoff: float) -> List[float]:
        return [row[0] for row in self._conn().execute(
            "SELECT at FROM hits WHERE bucket=? AND client=? AND at > ? ORDER BY at",
            (bucket, client, cutoff))]

    def evict_hits(self, bucket: str, cutoff: float) -> None:
        self._conn().execute("DELETE FROM hits WHERE bucket=? AND at <= ?",
                             (bucket, cutoff))

    # ── explore ──────────────────────────────────────────────────────────

    def add_questions(self, slug: str, items: List[dict]) -> None:
        """Insert the ones not seen before. The primary key does the dedup, so
        a question served three restarts ago still will not come back."""
        conn = self._conn()
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM questions WHERE slug=?",
                           (slug,)).fetchone()
        seq = row[0] if row else 0
        for item in items:
            seq += 1
            conn.execute(
                "INSERT OR IGNORE INTO questions (slug, key, question, hook, served, seq) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (slug, _key(item["question"]), item["question"],
                 item.get("hook", ""), seq))

    def take_questions(self, slug: str, count: int) -> List[dict]:
        conn = self._conn()
        rows: List[Tuple[str, str, str]] = list(conn.execute(
            "SELECT key, question, hook FROM questions "
            "WHERE slug=? AND served=0 ORDER BY seq LIMIT ?",
            (slug, count)))
        if rows:
            conn.executemany(
                "UPDATE questions SET served=1 WHERE slug=? AND key=?",
                [(slug, key) for key, _, _ in rows])
        return [{"question": question, "hook": hook} for _, question, hook in rows]

    def waiting_count(self, slug: str) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) FROM questions WHERE slug=? AND served=0",
            (slug,)).fetchone()
        return row[0] if row else 0

    def served_questions(self, slug: str, limit: int) -> List[str]:
        """The most recently served, oldest first — the order the prompt wants."""
        rows = list(self._conn().execute(
            "SELECT question FROM questions WHERE slug=? AND served=1 "
            "ORDER BY seq DESC LIMIT ?", (slug, limit)))
        return [question for (question,) in reversed(rows)]

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def _key(question: str) -> str:
    return " ".join(question.lower().split())


def open_store() -> Optional[StateStore]:
    """A store if STATE_DB names one, otherwise None — meaning stay in memory."""
    path = os.environ.get("STATE_DB", "").strip()
    return StateStore(path) if path else None
