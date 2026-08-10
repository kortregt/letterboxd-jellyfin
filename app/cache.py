"""SQLite-backed TTL cache with stale-while-error semantics.

Expired rows are kept rather than deleted, so that when an upstream source is
unreachable (Letterboxd goes down globally more often than you would like) the
app can serve the last known-good copy and say how old it is, instead of
showing an empty page.
"""

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass

from config import Config

# One connection guarded by a mutex, rather than one per thread. The previous
# thread-local approach leaked a connection for every short-lived pool thread.
# Writes here are small and infrequent, so the lock costs nothing meaningful.
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


@dataclass(frozen=True)
class Entry:
    value: object
    age_seconds: float
    is_stale: bool


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        directory = os.path.dirname(Config.CACHE_DB_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        _conn = sqlite3.connect(Config.CACHE_DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


def init_db():
    """Create the cache table, migrating older databases in place."""
    with _lock:
        conn = _connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at REAL,
                created_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(cache)")}
        if "created_at" not in columns:
            # Pre-existing database from before stale reads: backfill with now
            # so old rows are treated as fresh-ish rather than ancient.
            conn.execute(
                "ALTER TABLE cache ADD COLUMN created_at REAL NOT NULL DEFAULT 0"
            )
            conn.execute("UPDATE cache SET created_at = ?", (time.time(),))
        conn.commit()


def close_db():
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


class TTLCache:
    def __init__(self, ttl_seconds: int | None = None):
        self._ttl = (
            Config.CACHE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        )

    def get(self, key: str):
        """Return the value only if it is still fresh, else None."""
        entry = self.get_entry(key)
        return None if entry is None or entry.is_stale else entry.value

    def get_entry(self, key: str, allow_stale: bool = False) -> Entry | None:
        """Return an entry, optionally including expired-but-usable data.

        Entries older than STALE_MAX_AGE_SECONDS are never returned; past that
        point the data is misleading rather than helpful.
        """
        with _lock:
            row = _connect().execute(
                "SELECT value, expires_at, created_at FROM cache WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None

        value_json, expires_at, created_at = row
        now = time.time()
        age = max(0.0, now - (created_at or now))
        is_stale = expires_at is not None and now >= expires_at

        if is_stale:
            if not allow_stale:
                return None
            if Config.STALE_MAX_AGE_SECONDS and age > Config.STALE_MAX_AGE_SECONDS:
                return None

        try:
            value = json.loads(value_json)
        except json.JSONDecodeError:
            return None
        return Entry(value=value, age_seconds=age, is_stale=is_stale)

    def set(self, key: str, value, ttl: int | None = None):
        """Store a value. Pass ttl=0 for permanent (no expiry)."""
        effective_ttl = self._ttl if ttl is None else ttl
        now = time.time()
        expires_at = None if effective_ttl == 0 else now + effective_ttl
        payload = json.dumps(value)
        with _lock:
            conn = _connect()
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at, created_at) "
                "VALUES (?, ?, ?, ?)",
                (key, payload, expires_at, now),
            )
            conn.commit()

    def invalidate(self, key: str | None = None):
        """Expire entries without discarding them, so stale reads still work."""
        now = time.time()
        with _lock:
            conn = _connect()
            if key:
                conn.execute(
                    "UPDATE cache SET expires_at = ? WHERE key = ?", (now, key)
                )
            else:
                # Permanent entries (expires_at IS NULL) are immutable facts
                # like TMDB ids — there is no point re-fetching them.
                conn.execute(
                    "UPDATE cache SET expires_at = ? WHERE expires_at IS NOT NULL",
                    (now,),
                )
            conn.commit()


def prune(max_age_seconds: int | None = None) -> int:
    """Delete expired rows past the stale window. Returns rows removed."""
    limit = (
        Config.STALE_MAX_AGE_SECONDS
        if max_age_seconds is None
        else max_age_seconds
    )
    if not limit:
        return 0
    cutoff = time.time() - limit
    with _lock:
        conn = _connect()
        cursor = conn.execute(
            "DELETE FROM cache WHERE expires_at IS NOT NULL AND created_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount
