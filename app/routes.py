import random as _random
import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.jellyfin import JellyfinClient, cache as jellyfin_cache
from app.letterboxd import get_all_watchlists, cache as letterboxd_cache
from app.matcher import match_all
from app.cache import TTLCache
from config import Config
import requests.exceptions

router = APIRouter()
templates = Jinja2Templates(directory="templates")
jellyfin = JellyfinClient()
_match_cache = TTLCache(Config.CACHE_TTL_SECONDS)


def _get_matched_data() -> tuple[list[dict], list[dict], dict[str, str]]:
    """Get overlap + missing results, computed once and cached."""
    cached = _match_cache.get("matched")
    if cached is not None:
        return cached

    watchlists, errors = get_all_watchlists()
    jf_movies = jellyfin.get_all_movies_for_matching()
    overlap, missing = match_all(watchlists, jf_movies)

    result = (overlap, missing, errors)
    _match_cache.set("matched", result)
    return result


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/api/random-movie")
async def get_random_movie(
    genres: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    runtime_min: int | None = None,
    runtime_max: int | None = None,
):
    try:
        filters = {}
        if genres:
            filters["genres"] = genres.split(",")
        if year_min:
            filters["year_min"] = year_min
        if year_max:
            filters["year_max"] = year_max
        if runtime_min:
            filters["runtime_min"] = runtime_min
        if runtime_max:
            filters["runtime_max"] = runtime_max

        movie = await asyncio.to_thread(
            jellyfin.get_random_movie, filters if filters else None
        )
        if not movie:
            return JSONResponse(
                {"error": "No movies found matching the criteria"}, status_code=404
            )
        return movie
    except requests.exceptions.RequestException as e:
        return JSONResponse(
            {"error": f"Failed to connect to Jellyfin: {e}"}, status_code=503
        )


@router.get("/api/genres")
async def get_genres():
    try:
        genres = await asyncio.to_thread(jellyfin.get_all_genres)
        return {"genres": genres}
    except requests.exceptions.RequestException as e:
        return JSONResponse(
            {"error": f"Failed to connect to Jellyfin: {e}"}, status_code=503
        )


@router.get("/api/friends")
async def get_friends():
    return {"friends": Config.LETTERBOXD_FRIENDS}


@router.get("/api/watchlists")
async def get_watchlists():
    try:
        watchlists, errors = await asyncio.to_thread(get_all_watchlists)
        return {"watchlists": watchlists, "errors": errors}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/overlap")
async def get_overlap(jellyfin_only: bool = False):
    try:
        overlap, _, errors = await asyncio.to_thread(_get_matched_data)
        if jellyfin_only:
            overlap = [m for m in overlap if m["on_jellyfin"]]
        return {"movies": overlap, "errors": errors}
    except requests.exceptions.RequestException as e:
        return JSONResponse(
            {"error": f"Failed to connect to Jellyfin: {e}"}, status_code=503
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/overlap/random")
async def get_overlap_random(jellyfin_only: bool = False):
    try:
        overlap, _, errors = await asyncio.to_thread(_get_matched_data)
        if jellyfin_only:
            overlap = [m for m in overlap if m["on_jellyfin"]]
        if not overlap:
            return JSONResponse(
                {"error": "No overlapping movies found"}, status_code=404
            )
        return _random.choice(overlap)
    except requests.exceptions.RequestException as e:
        return JSONResponse(
            {"error": f"Failed to connect to Jellyfin: {e}"}, status_code=503
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/missing")
async def get_missing():
    try:
        _, missing, errors = await asyncio.to_thread(_get_matched_data)
        return {"movies": missing, "errors": errors}
    except requests.exceptions.RequestException as e:
        return JSONResponse(
            {"error": f"Failed to connect to Jellyfin: {e}"}, status_code=503
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/cache/refresh")
async def refresh_cache():
    jellyfin_cache.invalidate()
    letterboxd_cache.invalidate()
    _match_cache.invalidate()
    return {"status": "ok"}


@router.get("/health")
async def health():
    return {"status": "healthy"}
