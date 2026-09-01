"""
store.py — the user list, in SQLite.

SQLite rather than a JSON file because this runs unattended for weeks: a
crash mid-write to a JSON file can lose everyone, whereas SQLite writes are
transactional and leave the last good state intact.

We store a chat id and four preferences. No names, no usernames, no phone
numbers, no email — Telegram offers all of them and we need none.
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")

_lock = threading.Lock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id      INTEGER PRIMARY KEY,
    theater      TEXT,      -- 'AANEM' | 'AAOPK' | 'both'
    row_zone     TEXT,      -- 'back_rows' | 'back_half' | 'middle_back'
    row_position TEXT,      -- 'center' | 'any'
    times        TEXT,      -- 'evenings_weekends' | 'weekends' | 'anytime'
    party_size   TEXT,      -- '1' .. '5'
    together     TEXT,      -- 'yes' | 'no' | 'na'  (na = party of one)
    exact_rows   TEXT,      -- 'K,L,M' — overrides row_zone when set
    exact_dates  TEXT,      -- '2026-09-12,2026-09-13' — overrides times when set
    status       TEXT,      -- 'signup' | 'active' | 'paused' | 'stopped'
    step         TEXT,      -- which signup question they're on
    joined_at    TEXT
);

CREATE TABLE IF NOT EXISTS sent (
    chat_id   INTEGER,
    hash      TEXT,
    seat      TEXT,
    sent_at   TEXT,
    PRIMARY KEY (chat_id, hash, seat)
);

CREATE INDEX IF NOT EXISTS idx_sent_hash ON sent(hash);
"""


def connect():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
        os.chmod(DB_PATH, 0o600)
    return _conn


def _migrate(conn):
    """Add columns introduced after someone already had a database.

    Existing users default to a party of one, which is how the bot behaved
    before party size existed, so nobody's alerts change until they /reset.
    """
    have = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    for column, default in (("party_size", "'1'"), ("together", "'na'"),
                            ("exact_rows", "NULL"), ("exact_dates", "NULL")):
        if column not in have:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT "
                         f"DEFAULT {default}")
            if default != "NULL":
                conn.execute(f"UPDATE users SET {column} = {default} "
                             f"WHERE {column} IS NULL")


def _now():
    return datetime.now(timezone.utc).isoformat()


# --- users -----------------------------------------------------------------

def get(chat_id):
    with _lock:
        row = connect().execute(
            "SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
    return dict(row) if row else None


def upsert(chat_id, **fields):
    """Create the row if new, then set whichever fields were passed."""
    with _lock:
        c = connect()
        c.execute("INSERT OR IGNORE INTO users (chat_id, status, joined_at) "
                  "VALUES (?, 'signup', ?)", (chat_id, _now()))
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE users SET {cols} WHERE chat_id = ?",
                      (*fields.values(), chat_id))
        c.commit()


def active_users():
    with _lock:
        rows = connect().execute(
            "SELECT * FROM users WHERE status = 'active'").fetchall()
    return [dict(r) for r in rows]


def count(statuses=("active", "paused")):
    marks = ",".join("?" * len(statuses))
    with _lock:
        (n,) = connect().execute(
            f"SELECT COUNT(*) FROM users WHERE status IN ({marks})",
            statuses).fetchone()
    return n


def forget(chat_id):
    """Genuinely delete someone, rather than flagging them inactive."""
    with _lock:
        c = connect()
        c.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
        c.execute("DELETE FROM sent WHERE chat_id = ?", (chat_id,))
        c.commit()


# --- what we've already told people ----------------------------------------

def unsent(chat_id, hash_code, seats):
    if not seats:
        return []
    marks = ",".join("?" * len(seats))
    with _lock:
        rows = connect().execute(
            f"SELECT seat FROM sent WHERE chat_id = ? AND hash = ? "
            f"AND seat IN ({marks})", (chat_id, hash_code, *seats)).fetchall()
    already = {r["seat"] for r in rows}
    return [s for s in seats if s not in already]


def mark_sent(chat_id, hash_code, seats):
    with _lock:
        c = connect()
        c.executemany(
            "INSERT OR IGNORE INTO sent (chat_id, hash, seat, sent_at) "
            "VALUES (?, ?, ?, ?)",
            [(chat_id, hash_code, s, _now()) for s in seats])
        c.commit()


def prune(live_hashes):
    """Drop history for showtimes that have passed, so this can't grow forever."""
    if not live_hashes:
        return 0
    marks = ",".join("?" * len(live_hashes))
    with _lock:
        c = connect()
        cur = c.execute(f"DELETE FROM sent WHERE hash NOT IN ({marks})",
                        tuple(live_hashes))
        c.commit()
    return cur.rowcount
