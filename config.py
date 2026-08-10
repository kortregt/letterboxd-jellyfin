import os
from dotenv import load_dotenv

load_dotenv()


def _csv(name: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


def _pairs(name: str) -> dict[str, str]:
    """Parse `a=1,b=2` into a dict, ignoring malformed entries."""
    out = {}
    for pair in _csv(name):
        if "=" in pair:
            key, value = pair.split("=", 1)
            if key.strip():
                out[key.strip()] = value.strip()
    return out


def _int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


class Config:
    JELLYFIN_URL = os.getenv("JELLYFIN_URL", "").rstrip("/")
    JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
    JELLYFIN_USER_ID = os.getenv("JELLYFIN_USER_ID", "")

    LETTERBOXD_FRIENDS = _csv("LETTERBOXD_FRIENDS")
    LETTERBOXD_NICKNAMES = _pairs("LETTERBOXD_NICKNAMES")

    CACHE_TTL_SECONDS = _int("CACHE_TTL_SECONDS", 3600, minimum=0)
    CACHE_DB_PATH = os.getenv("CACHE_DB_PATH", "data/cache.db")

    # How long expired data may still be served when upstream is unreachable.
    # Letterboxd outages are the common case; a week-old watchlist beats none.
    STALE_MAX_AGE_SECONDS = _int("STALE_MAX_AGE_SECONDS", 7 * 24 * 3600, minimum=0)

    HTTP_TIMEOUT_SECONDS = _int("HTTP_TIMEOUT_SECONDS", 15, minimum=1)

    # Concurrency against Letterboxd. Deliberately modest: it is a free service
    # being scraped, and hammering it is how you get rate-limited.
    LETTERBOXD_MAX_WORKERS = _int("LETTERBOXD_MAX_WORKERS", 4, minimum=1)
    LETTERBOXD_RETRIES = _int("LETTERBOXD_RETRIES", 2, minimum=0)

    # Upper bound on a whole watchlist sync. letterboxdpy retries internally for
    # ~20s before surfacing a block, so without a ceiling one bad account holds
    # up the page. Whoever misses the deadline is served from cache instead.
    LETTERBOXD_DEADLINE_SECONDS = _int("LETTERBOXD_DEADLINE_SECONDS", 45, minimum=5)

    # Per-sync ceiling on per-film TMDB lookups. Results are cached forever, so
    # successive refreshes chip away at the backlog instead of one huge scrape.
    TMDB_RESOLVE_BUDGET = _int("TMDB_RESOLVE_BUDGET", 75, minimum=0)

    # A film on 2+ watchlists counts as "overlap". Raise it if you have many
    # friends, where "any two people" stops being a meaningful signal.
    OVERLAP_MIN_FRIENDS = _int("OVERLAP_MIN_FRIENDS", 2, minimum=2)

    # Letterboxd and Jellyfin disagree about release year surprisingly often.
    YEAR_TOLERANCE = _int("YEAR_TOLERANCE", 1, minimum=0)

    @staticmethod
    def errors() -> list[str]:
        """Return a list of human-readable configuration problems."""
        problems = []
        if not Config.JELLYFIN_URL:
            problems.append("JELLYFIN_URL is not set (e.g. http://jellyfin:8096)")
        elif not Config.JELLYFIN_URL.startswith(("http://", "https://")):
            problems.append(
                f"JELLYFIN_URL must start with http:// or https:// "
                f"(got {Config.JELLYFIN_URL!r})"
            )
        if not Config.JELLYFIN_API_KEY:
            problems.append(
                "JELLYFIN_API_KEY is not set "
                "(Jellyfin → Dashboard → API Keys → new key)"
            )
        if not Config.LETTERBOXD_FRIENDS:
            problems.append(
                "LETTERBOXD_FRIENDS is not set (comma-separated usernames)"
            )

        unknown = set(Config.LETTERBOXD_NICKNAMES) - set(Config.LETTERBOXD_FRIENDS)
        if unknown:
            problems.append(
                "LETTERBOXD_NICKNAMES refers to usernames missing from "
                f"LETTERBOXD_FRIENDS: {', '.join(sorted(unknown))}"
            )
        return problems

    @staticmethod
    def validate():
        problems = Config.errors()
        if problems:
            raise ValueError(
                "Invalid configuration:\n"
                + "\n".join(f"  - {p}" for p in problems)
            )
