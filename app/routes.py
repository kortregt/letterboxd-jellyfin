import asyncio
import hashlib
import os
import random as _random
import threading
import time

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.cache import TTLCache
from app.jellyfin import JellyfinClient
from app.letterboxd import get_all_watchlists
from app.matcher import match_all
from config import Config

router = APIRouter()
templates = Jinja2Templates(directory="templates")
jellyfin = JellyfinClient()

_match_cache = TTLCache()
# Single-flight: a full sync scrapes every watchlist, so two simultaneous cache
# misses must not both do it. Latecomers wait and reuse the first result.
_match_lock = threading.Lock()


def _compute_matched() -> dict:
    results = get_all_watchlists()
    watchlists = {name: r.movies for name, r in results.items()}
    jf_movies, jf_stale_age = jellyfin.get_all_movies_for_matching(allow_stale=True)

    overlap, missing, stats = match_all(watchlists, jf_movies)

    sources = {
        name: {
            "count": len(r.movies),
            "error": r.error,
            "stale_age": r.stale_age,
        }
        for name, r in results.items()
    }
    return {
        "overlap": overlap,
        "missing": missing,
        "stats": stats,
        "sources": sources,
        "jellyfin_stale_age": jf_stale_age,
        "generated_at": time.time(),
    }


def _get_matched_data() -> dict:
    cached = _match_cache.get("matched")
    if cached is not None:
        return cached
    with _match_lock:
        cached = _match_cache.get("matched")
        if cached is not None:
            return cached
        data = _compute_matched()
        _match_cache.set("matched", data)
        return data


def _parse_friends(raw: str | None) -> list[str]:
    """Accept only configured usernames, so the filter cannot be spoofed."""
    if not raw:
        return []
    known = set(Config.LETTERBOXD_FRIENDS)
    return [f.strip() for f in raw.split(",") if f.strip() in known]


def _filter_by_friends(movies: list[dict], friends: list[str], mode: str) -> list[dict]:
    """`any` = wanted by at least one selected friend; `all` = by every one."""
    if not friends:
        return movies
    wanted = set(friends)
    if mode == "all":
        return [m for m in movies if wanted.issubset(m["wanted_by"])]
    return [m for m in movies if wanted.intersection(m["wanted_by"])]


def _warnings(data: dict) -> list[str]:
    """Human-readable notes about degraded data, for display in the UI."""
    notes = []
    for name, source in data["sources"].items():
        label = Config.LETTERBOXD_NICKNAMES.get(name, name)
        if source["stale_age"] is not None:
            notes.append(
                f"{label}: showing a cached watchlist from "
                f"{_humanize(source['stale_age'])} ago (Letterboxd unreachable)"
            )
        elif source["error"]:
            notes.append(f"{label}: watchlist unavailable ({source['error']})")
    if data.get("jellyfin_stale_age") is not None:
        notes.append(
            f"Jellyfin library is a cached copy from "
            f"{_humanize(data['jellyfin_stale_age'])} ago"
        )
    return notes


def _humanize(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


# ── pages ──


_ASSETS = ("static/css/styles.css", "static/js/app.js")
_BOOT_ID = f"{time.time_ns():x}"


def asset_version() -> str:
    """Fingerprint the static files so a deploy cannot serve a stale cache.

    Starlette sends ETag and Last-Modified but no Cache-Control, which leaves
    browsers to guess a freshness window from the file's age — Firefox will
    happily hold a months-old stylesheet without ever revalidating. Changing
    the URL whenever the bytes change sidesteps the guesswork entirely.
    """
    parts = []
    for path in _ASSETS:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        parts.append(f"{stat.st_mtime_ns}-{stat.st_size}")
    if not parts:
        # Never fall back to a constant: that would cache badly forever.
        return _BOOT_ID
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"asset_version": asset_version()},
        # The page itself must never be cached, or a browser holding an old
        # copy would keep requesting the old asset URLs and defeat the point.
        headers={"Cache-Control": "no-cache"},
    )


# ── random picker ──


@router.get("/api/random-movie")
async def get_random_movie(
    genres: str | None = None,
    year_min: int | None = Query(None, ge=1870, le=2200),
    year_max: int | None = Query(None, ge=1870, le=2200),
    runtime_min: int | None = Query(None, ge=0, le=1000),
    runtime_max: int | None = Query(None, ge=0, le=1000),
):
    filters = {}
    if genres:
        filters["genres"] = [g for g in genres.split(",") if g.strip()]
    for key, value in (
        ("year_min", year_min),
        ("year_max", year_max),
        ("runtime_min", runtime_min),
        ("runtime_max", runtime_max),
    ):
        if value is not None:
            filters[key] = value

    movie = await asyncio.to_thread(jellyfin.get_random_movie, filters or None)
    if not movie:
        return JSONResponse(
            {"error": "No movies match those filters"}, status_code=404
        )
    return movie


@router.get("/api/genres")
async def get_genres():
    return {"genres": await asyncio.to_thread(jellyfin.get_all_genres)}


@router.get("/api/image/{item_id}")
async def get_image(item_id: str):
    """Proxy posters so the browser never needs to reach Jellyfin itself."""
    result = await asyncio.to_thread(jellyfin.get_image, item_id)
    if result is None:
        return Response(status_code=404)
    content, content_type = result
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ── watchlist views ──


@router.get("/api/friends")
async def get_friends():
    return {
        "friends": Config.LETTERBOXD_FRIENDS,
        "nicknames": Config.LETTERBOXD_NICKNAMES,
    }


@router.get("/api/overlap")
async def get_overlap(
    friends: str | None = None,
    match: str = Query("any", pattern="^(any|all)$"),
    jellyfin_only: bool = False,
):
    data = await asyncio.to_thread(_get_matched_data)
    movies = _filter_by_friends(data["overlap"], _parse_friends(friends), match)
    if jellyfin_only:
        movies = [m for m in movies if m["on_jellyfin"]]
    return {
        "movies": movies,
        "stats": data["stats"],
        "warnings": _warnings(data),
        "generated_at": data["generated_at"],
    }


@router.get("/api/overlap/random")
async def get_overlap_random(
    friends: str | None = None,
    match: str = Query("any", pattern="^(any|all)$"),
    jellyfin_only: bool = False,
):
    """Pick from the same filtered set the list is showing."""
    data = await asyncio.to_thread(_get_matched_data)
    movies = _filter_by_friends(data["overlap"], _parse_friends(friends), match)
    if jellyfin_only:
        movies = [m for m in movies if m["on_jellyfin"]]
    if not movies:
        return JSONResponse(
            {"error": "No films match the current filters"}, status_code=404
        )
    return _random.choice(movies)


@router.get("/api/missing")
async def get_missing(
    friends: str | None = None,
    match: str = Query("any", pattern="^(any|all)$"),
):
    data = await asyncio.to_thread(_get_matched_data)
    movies = _filter_by_friends(data["missing"], _parse_friends(friends), match)
    return {
        "movies": movies,
        "stats": data["stats"],
        "warnings": _warnings(data),
        "generated_at": data["generated_at"],
    }


# ── operations ──


@router.get("/api/status")
async def get_status():
    data = await asyncio.to_thread(_get_matched_data)
    return {
        "generated_at": data["generated_at"],
        "age": _humanize(time.time() - data["generated_at"]),
        "stats": data["stats"],
        "sources": data["sources"],
        "warnings": _warnings(data),
    }


@router.post("/api/cache/refresh")
async def refresh_cache():
    # Every cache shares one table, so a single sweep expires the lot. Entries
    # stored permanently (resolved TMDB ids) are deliberately left alone.
    _match_cache.invalidate()
    return {"status": "ok"}


@router.get("/health")
async def health():
    return {"status": "healthy"}
