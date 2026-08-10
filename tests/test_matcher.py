import pytest

from app.matcher import match_all, normalize_title, strip_article
from tests.conftest import film, jf


def match(watchlists, library, **kwargs):
    """Match with TMDB resolution off, so tests never touch the network."""
    kwargs.setdefault("tmdb_budget", 0)
    return match_all(watchlists, library, **kwargs)


class TestNormalizeTitle:
    def test_lowercases_and_strips_punctuation(self):
        assert normalize_title("The Grand Budapest Hotel!") == "the grand budapest hotel"

    def test_folds_accents(self):
        # Jellyfin and Letterboxd disagree about diacritics constantly.
        assert normalize_title("Léon") == normalize_title("Leon")
        assert normalize_title("Amélie") == normalize_title("Amelie")

    def test_expands_ampersand(self):
        assert normalize_title("Fire & Ice") == normalize_title("Fire and Ice")

    def test_collapses_whitespace(self):
        assert normalize_title("  Blade   Runner  ") == "blade runner"

    def test_handles_empty(self):
        assert normalize_title("") == ""

    def test_strip_article(self):
        assert strip_article("the thing") == "thing"
        assert strip_article("la haine") == "haine"
        assert strip_article("thing") == "thing"


class TestMatching:
    def test_exact_title_and_year(self):
        overlap, missing, _ = match(
            {"alice": [film("Heat", 1995)], "bob": [film("Heat", 1995)]},
            [jf("Heat", 1995)],
        )
        assert len(overlap) == 1
        assert overlap[0]["on_jellyfin"] is True
        assert missing == []

    def test_accented_title_matches_unaccented_library_entry(self):
        _, missing, _ = match(
            {"alice": [film("Léon: The Professional", 1994)]},
            [jf("Leon: The Professional", 1994)],
        )
        assert missing == [], "accent difference should not report a film as absent"

    def test_year_tolerance_absorbs_metadata_disagreement(self):
        # Letterboxd says 2019, the server says 2020 — same film.
        _, missing, _ = match(
            {"alice": [film("Parasite", 2019)]},
            [jf("Parasite", 2020)],
            year_tolerance=1,
        )
        assert missing == []

    def test_year_tolerance_is_bounded(self):
        _, missing, _ = match(
            {"alice": [film("Solaris", 1972)]},
            [jf("Solaris", 2002)],
            year_tolerance=1,
        )
        assert len(missing) == 1, "a remake 30 years later is a different film"

    def test_leading_article_difference(self):
        _, missing, _ = match(
            {"alice": [film("The Thing", 1982)]},
            [jf("Thing, The", 1982)],
        )
        assert missing == []

    def test_missing_film_is_reported(self):
        overlap, missing, _ = match(
            {"alice": [film("Stalker", 1979)], "bob": [film("Stalker", 1979)]},
            [jf("Heat", 1995)],
        )
        assert len(missing) == 1
        assert missing[0]["name"] == "Stalker"
        assert overlap[0]["on_jellyfin"] is False

    def test_ambiguous_title_without_year_is_not_matched(self):
        _, missing, _ = match(
            {"alice": [film("Solaris", None)]},
            [jf("Solaris", 1972), jf("Solaris", 2002)],
        )
        assert len(missing) == 1, "two candidates and no year is not a confident match"

    def test_unambiguous_title_without_year_matches(self):
        _, missing, _ = match(
            {"alice": [film("Stalker", None)]},
            [jf("Stalker", 1979)],
        )
        assert missing == []


class TestOverlap:
    def test_requires_minimum_friends(self):
        watchlists = {
            "alice": [film("Heat", 1995)],
            "bob": [film("Heat", 1995)],
            "carol": [film("Solaris", 1972)],
        }
        overlap, _, _ = match(watchlists, [], min_friends=2)
        assert [m["name"] for m in overlap] == ["Heat"]

    def test_threshold_is_configurable(self):
        watchlists = {
            "alice": [film("Heat", 1995)],
            "bob": [film("Heat", 1995)],
        }
        overlap, _, _ = match(watchlists, [], min_friends=3)
        assert overlap == [], "two friends should not clear a threshold of three"

    def test_wanted_by_lists_every_friend(self):
        watchlists = {
            "alice": [film("Heat", 1995)],
            "bob": [film("Heat", 1995)],
            "carol": [film("Heat", 1995)],
        }
        overlap, _, _ = match(watchlists, [])
        assert overlap[0]["wanted_by"] == ["alice", "bob", "carol"]

    def test_sorted_by_popularity_then_title(self):
        watchlists = {
            "alice": [film("Zodiac", 2007), film("Alien", 1979)],
            "bob": [film("Zodiac", 2007), film("Alien", 1979)],
            "carol": [film("Zodiac", 2007)],
        }
        overlap, _, _ = match(watchlists, [])
        assert [m["name"] for m in overlap] == ["Zodiac", "Alien"]

    def test_missing_excludes_films_present_on_server(self):
        watchlists = {"alice": [film("Heat", 1995), film("Stalker", 1979)]}
        _, missing, _ = match(watchlists, [jf("Heat", 1995)])
        assert [m["name"] for m in missing] == ["Stalker"]


class TestStats:
    def test_reports_counts(self):
        watchlists = {
            "alice": [film("Heat", 1995), film("Stalker", 1979)],
            "bob": [film("Heat", 1995)],
        }
        _, _, stats = match(watchlists, [jf("Heat", 1995)])
        assert stats["total_films"] == 2
        assert stats["on_jellyfin"] == 1
        assert stats["min_friends"] == 2


class TestEdgeCases:
    def test_empty_watchlists(self):
        overlap, missing, stats = match({}, [jf("Heat", 1995)])
        assert overlap == [] and missing == []
        assert stats["total_films"] == 0

    def test_empty_library_reports_everything_missing(self):
        overlap, missing, _ = match({"alice": [film("Heat", 1995)]}, [])
        assert len(missing) == 1
        assert overlap == []

    @pytest.mark.parametrize("bad_name", ["", None])
    def test_library_entries_without_names_are_skipped(self, bad_name):
        library = [{"jellyfin_id": "x", "name": bad_name, "year": 1995}]
        _, missing, _ = match({"alice": [film("Heat", 1995)]}, library)
        assert len(missing) == 1
