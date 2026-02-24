import time


class TTLCache:
    def __init__(self, ttl_seconds: int = 3600):
        self._store: dict[str, tuple] = {}
        self._ttl = ttl_seconds

    def get(self, key: str):
        if key in self._store:
            value, expiry = self._store[key]
            if time.time() < expiry:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value):
        self._store[key] = (value, time.time() + self._ttl)

    def invalidate(self, key: str | None = None):
        if key:
            self._store.pop(key, None)
        else:
            self._store.clear()
