"""Letterboxd watchlist scraping.

Scraping is the fragile half of this app: Letterboxd has no public API, goes
down occasionally, and will rate-limit an impatient client. Everything here is
built around degrading rather than failing — bounded concurrency, retries with
backoff, and a fall back to the last good copy when the site is unreachable.
"""

import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from letterboxdpy.movie import Movie
from letterboxdpy.user import User

from app.cache import TTLCache
from config import Config

cache = TTLCache()


@dataclass
class WatchlistResult:
    username: str
    movies: list[dict] = field(default_factory=list)
    error: str | None = None
    stale_age: float | None = None

    @property
    def is_stale(self) -> bool:
        return self.stale_age is not None


class PermanentError(Exception):
    """A failure that retrying cannot fix — a block, a 404, a private profile."""


def _classify(error: Exception) -> str | None:
    """Return a short human message if the failure is permanent, else None.

    Retrying these is pure cost: an IP block answers 403 just as fast the third
    time, and four friends x three attempts x backoff is over a minute of the
    user staring at a spinner for an answer we already had.
    """
    name = type(error).__name__
    text = str(error).lower()

    if name == "AccessDeniedError" or "403" in text or "blocked" in text:
        # Letterboxd sits behind Cloudflare, which letterboxdpy gets past by
        # impersonating a browser's TLS fingerprint. When Cloudflare moves on,
        # every request 403s until the library is updated — it reads as an IP
        # ban but usually is not one. See letterboxdpy issue #167.
        return (
            "Letterboxd refused the request (HTTP 403). Usually this means "
            "letterboxdpy is out of date — try `uv lock --upgrade-package "
            "letterboxdpy` — or that a VPN or proxy is in the way."
        )
    if name == "PrivateRouteError" or "private" in text:
        return "This account's watchlist is private."
    if "404" in text or "not found" in text:
        return "No such Letterboxd account."
    return None


def _with_retries(operation, what: str):
    """Run `operation`, retrying transient failures with backoff and jitter."""
    attempts = Config.LETTERBOXD_RETRIES + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as e:  # letterboxdpy raises assorted bare exceptions
            permanent = _classify(e)
            if permanent:
                raise PermanentError(permanent) from e
            last_error = e
            if attempt < attempts - 1:
                # Jitter matters: without it, every friend's request retries in
                # lockstep and we hit Letterboxd in synchronised bursts.
                delay = (2**attempt) + random.uniform(0, 0.5)
                time.sleep(delay)
    raise RuntimeError(
        f"{what} is not responding (gave up after {attempts} attempts)"
    )


def fetch_tmdb_ids(slugs: list[str], budget: int | None = None) -> dict[str, str]:
    """Resolve Letterboxd slugs to TMDB ids. Cached permanently once known.

    `budget` caps how many *uncached* lookups this call performs, not how many
    slugs it considers. Applying the cap to the input instead would let already
    resolved slugs eat the whole allowance, so the same films would be selected
    every sync and the backlog would never move. Callers should pass slugs in
    priority order.
    """
    if not slugs:
        return {}

    resolved: dict[str, str] = {}
    pending: list[str] = []
    for slug in slugs:
        cached = cache.get(f"tmdb:{slug}")
        if cached is not None:
            # Negative results are cached as "" so we stop re-scraping films
            # that genuinely have no TMDB link.
            if cached:
                resolved[slug] = cached
        else:
            pending.append(slug)

    if budget is not None:
        pending = pending[:budget]

    if not pending:
        return resolved

    def _fetch_one(slug: str) -> tuple[str, str | None]:
        try:
            link = _with_retries(lambda: Movie(slug).tmdb_link, f"TMDB lookup {slug}")
            if link:
                match = re.search(r"/movie/(\d+)", link)
                if match:
                    return slug, match.group(1)
        except Exception:
            return slug, None
        return slug, ""

    with ThreadPoolExecutor(max_workers=Config.LETTERBOXD_MAX_WORKERS) as executor:
        for slug, tmdb_id in executor.map(_fetch_one, pending):
            if tmdb_id is None:
                continue  # transient failure: leave uncached so we retry later
            cache.set(f"tmdb:{slug}", tmdb_id, ttl=0)
            if tmdb_id:
                resolved[slug] = tmdb_id

    return resolved


def _friendly(error: Exception) -> str:
    """Collapse an error to one readable line fit for the UI.

    letterboxdpy raises with a multi-line JSON payload attached; dumping that
    into a banner is unreadable and leaks nothing useful.
    """
    text = " ".join(str(error).split())
    return text[:180] if text else type(error).__name__


def get_watchlist(username: str) -> WatchlistResult:
    """Fetch one watchlist, falling back to stale cache when Letterboxd is down."""
    key = f"watchlist:{username}"

    entry = cache.get_entry(key)
    if entry is not None:
        return WatchlistResult(username=username, movies=entry.value)

    try:
        raw = _with_retries(
            lambda: User(username).get_watchlist_movies(),
            f"Watchlist for {username}",
        )
    except Exception as e:
        message = _friendly(e)
        stale = cache.get_entry(key, allow_stale=True)
        if stale is not None:
            return WatchlistResult(
                username=username,
                movies=stale.value,
                error=message,
                stale_age=stale.age_seconds,
            )
        return WatchlistResult(username=username, error=message)

    movies = [
        {
            "name": data.get("name", ""),
            "year": data.get("year"),
            "slug": data.get("slug", ""),
            "url": data.get("url", ""),
            "letterboxd_id": str(film_id),
        }
        for film_id, data in (raw or {}).items()
    ]
    cache.set(key, movies)
    return WatchlistResult(username=username, movies=movies)


def _fallback_result(username: str, message: str) -> WatchlistResult:
    """Best available answer for a user we could not fetch."""
    stale = cache.get_entry(f"watchlist:{username}", allow_stale=True)
    if stale is not None:
        return WatchlistResult(
            username=username,
            movies=stale.value,
            error=message,
            stale_age=stale.age_seconds,
        )
    return WatchlistResult(username=username, error=message)


def get_all_watchlists() -> dict[str, WatchlistResult]:
    """Fetch every friend's watchlist, isolating per-user failures.

    A deadline bounds the whole batch. letterboxdpy retries a 403 five times
    with escalating sleeps before giving up — around twenty seconds we cannot
    configure away — so without this the page waits on the slowest upstream
    failure. Threads that miss the deadline are abandoned rather than killed;
    if one later succeeds it still populates the cache for the next request.
    """
    friends = Config.LETTERBOXD_FRIENDS
    if not friends:
        return {}

    workers = min(len(friends), Config.LETTERBOXD_MAX_WORKERS)
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            username: executor.submit(get_watchlist, username)
            for username in friends
        }
        deadline = time.monotonic() + Config.LETTERBOXD_DEADLINE_SECONDS
        results = {}
        for username, future in futures.items():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                results[username] = future.result(timeout=remaining)
            except FutureTimeout:
                future.cancel()
                results[username] = _fallback_result(
                    username, "Letterboxd did not respond in time"
                )
            except Exception as e:
                results[username] = _fallback_result(username, _friendly(e))
        return results
    finally:
        # Do not wait: an abandoned fetch finishing in the background is fine.
        executor.shutdown(wait=False)
