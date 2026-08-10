"""Tests for query construction and filtering — the two places that were
quietly returning wrong answers rather than failing loudly.
"""

from datetime import date

from app.jellyfin import JellyfinClient
from app.routes import _filter_by_friends, _parse_friends


def years(params) -> list[int]:
    return [int(y) for y in params["Years"].split(",")]


class TestYearFilter:
    def setup_method(self):
        self.client = JellyfinClient()

    def test_open_ended_minimum_covers_everything_after_it(self):
        # This is the bug: "1990 onwards" used to collapse to exactly 1990.
        params = self.client._items_params({"year_min": 1990}, "Id")
        span = years(params)
        assert min(span) == 1990
        assert max(span) >= date.today().year
        assert 2001 in span

    def test_open_ended_maximum_covers_everything_before_it(self):
        params = self.client._items_params({"year_max": 1979}, "Id")
        span = years(params)
        assert max(span) == 1979
        assert 1950 in span

    def test_closed_range(self):
        params = self.client._items_params(
            {"year_min": 1970, "year_max": 1975}, "Id"
        )
        assert years(params) == [1970, 1971, 1972, 1973, 1974, 1975]

    def test_reversed_range_is_corrected(self):
        params = self.client._items_params(
            {"year_min": 1975, "year_max": 1970}, "Id"
        )
        assert years(params) == [1970, 1971, 1972, 1973, 1974, 1975]

    def test_single_year(self):
        params = self.client._items_params(
            {"year_min": 1994, "year_max": 1994}, "Id"
        )
        assert years(params) == [1994]

    def test_no_year_filter_omits_the_parameter(self):
        assert "Years" not in self.client._items_params({}, "Id")
        assert "Years" not in self.client._items_params(None, "Id")


class TestGenreFilter:
    def setup_method(self):
        self.client = JellyfinClient()

    def test_multiple_genres_are_pipe_delimited(self):
        # Jellyfin expects pipes here; commas were read as one long genre name.
        params = self.client._items_params({"genres": ["Horror", "Comedy"]}, "Id")
        assert params["Genres"] == "Horror|Comedy"

    def test_single_genre(self):
        params = self.client._items_params({"genres": ["Horror"]}, "Id")
        assert params["Genres"] == "Horror"


class TestFriendParsing:
    def test_accepts_configured_usernames(self):
        assert _parse_friends("alice,bob") == ["alice", "bob"]

    def test_rejects_unknown_usernames(self):
        assert _parse_friends("alice,mallory") == ["alice"]

    def test_empty_input(self):
        assert _parse_friends("") == []
        assert _parse_friends(None) == []


class TestFriendFiltering:
    movies = [
        {"name": "Heat", "wanted_by": ["alice", "bob"]},
        {"name": "Stalker", "wanted_by": ["alice"]},
        {"name": "Alien", "wanted_by": ["alice", "bob", "carol"]},
    ]

    def test_any_mode_includes_films_wanted_by_one_selected_friend(self):
        result = _filter_by_friends(self.movies, ["bob"], "any")
        assert [m["name"] for m in result] == ["Heat", "Alien"]

    def test_all_mode_requires_every_selected_friend(self):
        result = _filter_by_friends(self.movies, ["alice", "bob"], "all")
        assert [m["name"] for m in result] == ["Heat", "Alien"]

    def test_all_mode_with_three_friends(self):
        result = _filter_by_friends(self.movies, ["alice", "bob", "carol"], "all")
        assert [m["name"] for m in result] == ["Alien"]

    def test_no_selection_returns_everything(self):
        # Deselecting every chip used to silently show the unfiltered list.
        assert _filter_by_friends(self.movies, [], "all") == self.movies
        assert _filter_by_friends(self.movies, [], "any") == self.movies

    def test_any_mode_is_the_union_not_the_whole_list(self):
        result = _filter_by_friends(self.movies, ["carol"], "any")
        assert [m["name"] for m in result] == ["Alien"]


class TestAssetVersion:
    """Fingerprinted asset URLs, so a deploy cannot serve a stale stylesheet."""

    def test_is_a_short_stable_fingerprint(self):
        from app.routes import asset_version

        first = asset_version()
        assert len(first) == 12
        assert asset_version() == first, "unchanged files must not churn the URL"

    def test_changes_when_an_asset_changes(self, tmp_path, monkeypatch):
        from app import routes

        asset = tmp_path / "styles.css"
        asset.write_text("body { color: red }")
        monkeypatch.setattr(routes, "_ASSETS", (str(asset),))
        before = routes.asset_version()

        asset.write_text("body { color: blue } .segmented { display: flex }")
        assert routes.asset_version() != before

    def test_falls_back_to_a_per_boot_value_when_assets_are_missing(
        self, monkeypatch
    ):
        from app import routes

        monkeypatch.setattr(routes, "_ASSETS", ("does/not/exist.css",))
        # A constant fallback would cache badly forever.
        assert routes.asset_version() == routes._BOOT_ID

    def test_template_requests_the_versioned_urls(self):
        markup = open("templates/index.html").read()
        assert "styles.css?v={{ asset_version }}" in markup
        assert "app.js?v={{ asset_version }}" in markup
