import os
import tempfile

# Must be set before anything imports config, which reads the environment once.
os.environ.setdefault("JELLYFIN_URL", "http://jellyfin.test")
os.environ.setdefault("JELLYFIN_API_KEY", "test-key")
os.environ.setdefault("LETTERBOXD_FRIENDS", "alice,bob,carol")
os.environ.setdefault("LETTERBOXD_NICKNAMES", "alice=Alice,bob=Bob")
os.environ.setdefault(
    "CACHE_DB_PATH", os.path.join(tempfile.mkdtemp(), "test-cache.db")
)

import pytest  # noqa: E402

from app import cache as cache_module  # noqa: E402
from config import Config  # noqa: E402


@pytest.fixture
def cache(tmp_path):
    """A TTLCache backed by a fresh database per test."""
    original_path = Config.CACHE_DB_PATH
    Config.CACHE_DB_PATH = str(tmp_path / "cache.db")
    cache_module.close_db()
    cache_module.init_db()
    yield cache_module.TTLCache(ttl_seconds=60)
    cache_module.close_db()
    Config.CACHE_DB_PATH = original_path


def film(name, year=None, slug=None, **extra):
    """A Letterboxd watchlist entry."""
    return {
        "name": name,
        "year": year,
        "slug": slug or name.lower().replace(" ", "-"),
        "url": f"https://letterboxd.com/film/{slug or 'x'}/",
        **extra,
    }


def jf(name, year=None, tmdb_id=None, jellyfin_id=None):
    """A Jellyfin library entry."""
    return {
        "jellyfin_id": jellyfin_id or f"jf-{name.lower().replace(' ', '-')}",
        "name": name,
        "year": year,
        "tmdb_id": tmdb_id,
        "imdb_id": None,
    }
