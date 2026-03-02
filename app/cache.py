import json
import sqlite3
import time
import threading

_DB_PATH = "data/cache.db"
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """One connection per thread (SQLite requirement)."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(_DB_PATH)
        _local.conn.execute("PRAGMA journal_mode=WAL")
    return _local.conn


def init_db():
    """Create the cache table if it doesn't exist."""
    import os
    os.makedirs("data", exist_ok=True)
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            expires_at REAL
        )
    """)
    conn.commit()


class TTLCache:
    def __init__(self, ttl_seconds: int = 3600):
        self._ttl = ttl_seconds

    def get(self, key: str):
        conn = _get_conn()
        row = conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        value_json, expires_at = row
        if expires_at is not None and time.time() >= expires_at:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
            return None
        return json.loads(value_json)

    def set(self, key: str, value, ttl: int | None = None):
        """Store a value. Pass ttl=0 for permanent (no expiry)."""
        conn = _get_conn()
        effective_ttl = ttl if ttl is not None else self._ttl
        expires_at = None if effective_ttl == 0 else time.time() + effective_ttl
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), expires_at),
        )
        conn.commit()

    def invalidate(self, key: str | None = None):
        conn = _get_conn()
        if key:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
        else:
            conn.execute("DELETE FROM cache")
        conn.commit()
