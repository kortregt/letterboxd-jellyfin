import time

from app import cache as cache_module

# A negative TTL writes a row that is already expired, which lets these tests
# exercise staleness without sleeping.
ALREADY_EXPIRED = -1


class TestBasics:
    def test_round_trips_values(self, cache):
        cache.set("k", {"a": [1, 2, 3]})
        assert cache.get("k") == {"a": [1, 2, 3]}

    def test_missing_key_returns_none(self, cache):
        assert cache.get("nope") is None

    def test_overwrites(self, cache):
        cache.set("k", "first")
        cache.set("k", "second")
        assert cache.get("k") == "second"

    def test_zero_ttl_never_expires(self, cache):
        cache.set("permanent", "value", ttl=0)
        entry = cache.get_entry("permanent")
        assert entry is not None and entry.is_stale is False


class TestExpiry:
    def test_expired_value_is_not_returned_by_get(self, cache):
        cache.set("k", "value", ttl=ALREADY_EXPIRED)
        assert cache.get("k") is None

    def test_expired_value_is_still_available_as_stale(self, cache):
        cache.set("k", "value", ttl=ALREADY_EXPIRED)
        entry = cache.get_entry("k", allow_stale=True)
        assert entry is not None
        assert entry.value == "value"
        assert entry.is_stale is True

    def test_reading_does_not_destroy_stale_data(self, cache):
        # The old implementation deleted on read, so an outage that outlived
        # one page load left nothing to fall back on.
        cache.set("k", "value", ttl=ALREADY_EXPIRED)
        assert cache.get("k") is None
        assert cache.get_entry("k", allow_stale=True).value == "value"

    def test_stale_entry_reports_age(self, cache):
        cache.set("k", "value", ttl=ALREADY_EXPIRED)
        entry = cache.get_entry("k", allow_stale=True)
        assert entry.age_seconds >= 0

    def test_data_past_the_stale_window_is_withheld(self, cache, monkeypatch):
        from config import Config

        cache.set("k", "value", ttl=ALREADY_EXPIRED)
        _age_row(cache, "k", seconds=10_000)
        monkeypatch.setattr(Config, "STALE_MAX_AGE_SECONDS", 60)
        assert cache.get_entry("k", allow_stale=True) is None


class TestInvalidate:
    def test_single_key(self, cache):
        cache.set("a", 1)
        cache.set("b", 2)
        cache.invalidate("a")
        assert cache.get("a") is None
        assert cache.get("b") == 2

    def test_all_keys(self, cache):
        cache.set("a", 1)
        cache.set("b", 2)
        cache.invalidate()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_preserves_permanent_entries(self, cache):
        # Resolved TMDB ids never change, so a refresh should not throw them
        # away and force the whole backlog to be scraped again.
        cache.set("tmdb:heat", "949", ttl=0)
        cache.set("watchlist:alice", [], ttl=60)
        cache.invalidate()
        assert cache.get("tmdb:heat") == "949"
        assert cache.get("watchlist:alice") is None

    def test_invalidated_data_survives_as_stale(self, cache):
        cache.set("k", "value")
        cache.invalidate()
        assert cache.get_entry("k", allow_stale=True).value == "value"


class TestPrune:
    def test_removes_entries_past_the_window(self, cache):
        cache.set("old", "value", ttl=ALREADY_EXPIRED)
        _age_row(cache, "old", seconds=10_000)
        assert cache_module.prune(max_age_seconds=60) == 1
        assert cache.get_entry("old", allow_stale=True) is None

    def test_keeps_recent_entries(self, cache):
        cache.set("fresh", "value")
        assert cache_module.prune(max_age_seconds=60) == 0
        assert cache.get("fresh") == "value"

    def test_keeps_permanent_entries(self, cache):
        cache.set("tmdb:heat", "949", ttl=0)
        _age_row(cache, "tmdb:heat", seconds=10_000_000)
        assert cache_module.prune(max_age_seconds=60) == 0
        assert cache.get("tmdb:heat") == "949"


def _age_row(cache, key, seconds):
    """Backdate a row so age-based behaviour can be tested without waiting."""
    conn = cache_module._connect()
    with cache_module._lock:
        conn.execute(
            "UPDATE cache SET created_at = ? WHERE key = ?",
            (time.time() - seconds, key),
        )
        conn.commit()
