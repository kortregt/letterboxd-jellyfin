import pytest

from app import letterboxd
from app.letterboxd import PermanentError, _classify, _friendly, _with_retries
from config import Config


class Boom(Exception):
    pass


class AccessDeniedError(Exception):
    """Stands in for letterboxdpy's own class, matched by name."""


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(Config, "LETTERBOXD_RETRIES", 2)
    monkeypatch.setattr(letterboxd.time, "sleep", lambda _: None)


class TestClassify:
    def test_recognises_letterboxds_block_by_class_name(self):
        assert "HTTP 403" in _classify(AccessDeniedError("nope"))

    def test_recognises_block_by_status_code(self):
        assert _classify(Boom('{"code": 403, "message": "IP or VPN Blocked"}'))

    def test_recognises_missing_account(self):
        assert _classify(Boom("404 not found")) == "No such Letterboxd account."

    def test_recognises_private_profile(self):
        assert "private" in _classify(Boom("PrivateRouteError: private")).lower()

    def test_transient_failures_are_not_classified(self):
        assert _classify(Boom("connection reset by peer")) is None


class TestRetries:
    def test_returns_on_success(self):
        assert _with_retries(lambda: "ok", "thing") == "ok"

    def test_retries_transient_failures(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise Boom("connection reset")
            return "ok"

        assert _with_retries(flaky, "thing") == "ok"
        assert len(calls) == 3

    def test_gives_up_after_the_configured_attempts(self):
        calls = []

        def always_fails():
            calls.append(1)
            raise Boom("connection reset")

        with pytest.raises(RuntimeError):
            _with_retries(always_fails, "thing")
        assert len(calls) == 3, "one initial attempt plus two retries"

    def test_does_not_retry_a_permanent_failure(self):
        # An IP block answers 403 just as fast the third time. Retrying it used
        # to cost over a minute of spinner before showing the same message.
        calls = []

        def blocked():
            calls.append(1)
            raise AccessDeniedError("403 blocked")

        with pytest.raises(PermanentError):
            _with_retries(blocked, "thing")
        assert len(calls) == 1

    def test_failure_message_has_no_json_payload(self):
        def always_fails():
            raise Boom('{\n "code": 500,\n "reason": "..."\n}')

        with pytest.raises(RuntimeError) as caught:
            _with_retries(always_fails, "Watchlist for alice")
        assert "\n" not in str(caught.value)


class TestFriendlyMessages:
    def test_collapses_multiline_payloads(self):
        assert "\n" not in _friendly(Boom('{\n "code": 403\n}'))

    def test_truncates_long_messages(self):
        assert len(_friendly(Boom("x" * 500))) <= 180

    def test_falls_back_to_the_class_name(self):
        assert _friendly(Boom("")) == "Boom"


class TestWatchlistFallback:
    def _stub_user(self, monkeypatch, result=None, error=None):
        class StubUser:
            def __init__(self, username):
                pass

            def get_watchlist_movies(self):
                if error:
                    raise error
                return result

        monkeypatch.setattr(letterboxd, "User", StubUser)

    def test_successful_fetch_is_cached(self, cache, monkeypatch):
        self._stub_user(
            monkeypatch,
            result={"1": {"name": "Heat", "year": 1995, "slug": "heat", "url": "u"}},
        )
        result = letterboxd.get_watchlist("alice")
        assert [m["name"] for m in result.movies] == ["Heat"]
        assert result.error is None
        assert cache.get("watchlist:alice") is not None

    def test_falls_back_to_stale_data_when_letterboxd_fails(self, cache, monkeypatch):
        # The whole point: an outage should degrade, not blank the page.
        cache.set("watchlist:alice", [{"name": "Heat", "year": 1995}], ttl=-1)
        self._stub_user(monkeypatch, error=AccessDeniedError("403 blocked"))

        result = letterboxd.get_watchlist("alice")
        assert [m["name"] for m in result.movies] == ["Heat"]
        assert result.is_stale is True
        assert result.stale_age is not None
        assert "HTTP 403" in result.error

    def test_reports_an_error_when_there_is_nothing_cached(self, cache, monkeypatch):
        self._stub_user(monkeypatch, error=AccessDeniedError("403 blocked"))
        result = letterboxd.get_watchlist("alice")
        assert result.movies == []
        assert result.is_stale is False
        assert "HTTP 403" in result.error

    def test_fresh_cache_avoids_the_network_entirely(self, cache, monkeypatch):
        cache.set("watchlist:alice", [{"name": "Cached", "year": 2000}])

        def explode(_):
            raise AssertionError("should not have hit the network")

        monkeypatch.setattr(letterboxd, "User", explode)
        assert letterboxd.get_watchlist("alice").movies[0]["name"] == "Cached"


class TestTmdbBudget:
    """The budget caps uncached lookups, so repeated syncs make progress."""

    def _count_lookups(self, monkeypatch):
        calls = []

        class StubMovie:
            def __init__(self, slug):
                calls.append(slug)
                self.tmdb_link = f"https://themoviedb.org/movie/{len(calls)}"

        monkeypatch.setattr(letterboxd, "Movie", StubMovie)
        return calls

    def test_budget_limits_lookups(self, cache, monkeypatch):
        calls = self._count_lookups(monkeypatch)
        letterboxd.fetch_tmdb_ids([f"film-{i}" for i in range(10)], budget=3)
        assert len(calls) == 3

    def test_cached_slugs_do_not_consume_budget(self, cache, monkeypatch):
        # The bug this guards: capping the input list meant already-resolved
        # films ate the whole allowance and the backlog never moved.
        for i in range(5):
            cache.set(f"tmdb:film-{i}", str(100 + i), ttl=0)
        calls = self._count_lookups(monkeypatch)

        result = letterboxd.fetch_tmdb_ids([f"film-{i}" for i in range(10)], budget=3)

        assert len(calls) == 3, "budget should apply to uncached lookups only"
        assert set(calls) == {"film-5", "film-6", "film-7"}
        assert result["film-0"] == "100", "cached ids still returned"

    def test_successive_syncs_work_through_the_backlog(self, cache, monkeypatch):
        slugs = [f"film-{i}" for i in range(10)]
        seen = []
        for _ in range(4):
            calls = self._count_lookups(monkeypatch)
            letterboxd.fetch_tmdb_ids(slugs, budget=3)
            seen.extend(calls)
        assert len(set(seen)) == 10, "every slug resolved after enough syncs"

    def test_no_budget_resolves_everything(self, cache, monkeypatch):
        calls = self._count_lookups(monkeypatch)
        letterboxd.fetch_tmdb_ids([f"film-{i}" for i in range(6)])
        assert len(calls) == 6
