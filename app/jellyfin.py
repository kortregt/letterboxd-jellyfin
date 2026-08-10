import random
import threading

import requests

from app.cache import TTLCache
from config import Config

cache = TTLCache()

# Films predate 1900, but nothing in a Jellyfin library predates the Lumières.
_MIN_FILM_YEAR = 1870
# Guard against pathological year ranges producing a multi-kilobyte query string.
_MAX_YEAR_SPAN = 200

_TICKS_PER_MINUTE = 600_000_000


class JellyfinError(RuntimeError):
    """Raised when Jellyfin cannot be reached or returns an error."""


class JellyfinClient:
    def __init__(self):
        self.server_url = Config.JELLYFIN_URL
        self._user_id = Config.JELLYFIN_USER_ID or None
        self._user_id_lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers.update({"X-Emby-Token": Config.JELLYFIN_API_KEY})

    # ── plumbing ──

    def _get(self, path: str, params: dict | None = None, stream: bool = False):
        """GET with a timeout on every call, normalising failures."""
        try:
            response = self._session.get(
                f"{self.server_url}{path}",
                params=params,
                timeout=Config.HTTP_TIMEOUT_SECONDS,
                stream=stream,
            )
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout as e:
            raise JellyfinError(
                f"Jellyfin did not respond within {Config.HTTP_TIMEOUT_SECONDS}s"
            ) from e
        except requests.exceptions.RequestException as e:
            raise JellyfinError(f"Could not reach Jellyfin: {e}") from e

    def _get_user_id(self) -> str:
        if self._user_id:
            return self._user_id
        with self._user_id_lock:
            if self._user_id:
                return self._user_id
            users = self._get("/Users").json()
            if not users:
                raise JellyfinError("No users found on the Jellyfin server")
            # Prefer an administrator: their view of the library is the complete
            # one, whereas a restricted account may not see every folder.
            admin = next(
                (u for u in users if u.get("Policy", {}).get("IsAdministrator")),
                None,
            )
            self._user_id = (admin or users[0])["Id"]
            return self._user_id

    def _items_params(self, filters: dict | None, fields: str) -> dict:
        params = {
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "Fields": fields,
        }
        if not filters:
            return params

        if filters.get("genres"):
            params["Genres"] = "|".join(filters["genres"])

        year_min = filters.get("year_min")
        year_max = filters.get("year_max")
        if year_min or year_max:
            # Jellyfin filters production year via an explicit list, so an
            # open-ended bound has to be expanded rather than left blank —
            # omitting the other end used to collapse the range to one year.
            low = int(year_min) if year_min else _MIN_FILM_YEAR
            high = int(year_max) if year_max else _current_year() + 1
            if low > high:
                low, high = high, low
            if high - low <= _MAX_YEAR_SPAN:
                params["Years"] = ",".join(str(y) for y in range(low, high + 1))
        return params

    def _fetch_items(self, params: dict) -> list[dict]:
        user_id = self._get_user_id()
        response = self._get(f"/Users/{user_id}/Items", params=params)
        return response.json().get("Items", [])

    # ── queries ──

    def get_random_movie(self, filters: dict | None = None) -> dict | None:
        """Pick one random movie, letting Jellyfin do the work where possible."""
        filters = filters or {}
        runtime_min = filters.get("runtime_min")
        runtime_max = filters.get("runtime_max")

        if not (runtime_min or runtime_max):
            # The common case: Jellyfin can sort randomly and return a single
            # item, so we transfer one movie instead of the whole library.
            params = self._items_params(filters, _DETAIL_FIELDS)
            params["SortBy"] = "Random"
            params["Limit"] = "1"
            items = self._fetch_items(params)
            return self._format_movie(items[0]) if items else None

        # Runtime has no server-side filter, so we need the candidate set — but
        # only ids and durations, not overviews and genres for every film.
        params = self._items_params(filters, "RunTimeTicks")
        candidates = [
            item
            for item in self._fetch_items(params)
            if _runtime_matches(item.get("RunTimeTicks"), runtime_min, runtime_max)
        ]
        if not candidates:
            return None
        chosen = random.choice(candidates)
        detailed = self.get_item(chosen["Id"])
        return self._format_movie(detailed or chosen)

    def get_item(self, item_id: str) -> dict | None:
        user_id = self._get_user_id()
        try:
            response = self._get(
                f"/Users/{user_id}/Items/{item_id}",
                params={"Fields": _DETAIL_FIELDS},
            )
        except JellyfinError:
            return None
        return response.json()

    def get_all_movies_for_matching(self, allow_stale: bool = False) -> tuple[list[dict], float | None]:
        """Return (movies, stale_age_seconds). Age is None when data is fresh."""
        entry = cache.get_entry("jellyfin:movies")
        if entry is not None:
            return entry.value, None

        try:
            items = self._fetch_items(
                {
                    "IncludeItemTypes": "Movie",
                    "Recursive": "true",
                    "Fields": "ProductionYear,ProviderIds",
                }
            )
        except JellyfinError:
            if allow_stale:
                stale = cache.get_entry("jellyfin:movies", allow_stale=True)
                if stale is not None:
                    return stale.value, stale.age_seconds
            raise

        movies = []
        for item in items:
            provider_ids = item.get("ProviderIds", {}) or {}
            movies.append(
                {
                    "jellyfin_id": item["Id"],
                    "name": item.get("Name", ""),
                    "year": item.get("ProductionYear"),
                    "tmdb_id": provider_ids.get("Tmdb"),
                    "imdb_id": provider_ids.get("Imdb"),
                }
            )
        cache.set("jellyfin:movies", movies)
        return movies, None

    def get_all_genres(self) -> list[str]:
        cached = cache.get("jellyfin:genres")
        if cached is not None:
            return cached
        user_id = self._get_user_id()
        response = self._get(
            "/Genres",
            params={"IncludeItemTypes": "Movie", "UserId": user_id},
        )
        genres = [g["Name"] for g in response.json().get("Items", [])]
        cache.set("jellyfin:genres", genres)
        return genres

    def get_image(self, item_id: str) -> tuple[bytes, str] | None:
        """Fetch a poster so the browser never talks to Jellyfin directly."""
        try:
            response = self._get(
                f"/Items/{item_id}/Images/Primary",
                params={"maxWidth": "480", "quality": "90"},
            )
        except JellyfinError:
            return None
        content_type = response.headers.get("Content-Type", "image/jpeg")
        return response.content, content_type

    # ── formatting ──

    def _format_movie(self, movie: dict) -> dict:
        ticks = movie.get("RunTimeTicks")
        item_id = movie.get("Id")
        return {
            "id": item_id,
            "name": movie.get("Name"),
            "year": movie.get("ProductionYear"),
            "runtime": round(ticks / _TICKS_PER_MINUTE) if ticks else None,
            "genres": movie.get("Genres", []),
            "overview": movie.get("Overview", ""),
            "community_rating": movie.get("CommunityRating"),
            "official_rating": movie.get("OfficialRating"),
            # Served through this app: JELLYFIN_URL is frequently an internal
            # address the browser cannot resolve, and publishing it leaks the
            # layout of the homelab to anyone who opens the page.
            "image_url": f"/api/image/{item_id}" if item_id else None,
        }


_DETAIL_FIELDS = (
    "Overview,Genres,RunTimeTicks,ProductionYear,ProviderIds,"
    "CommunityRating,OfficialRating"
)


def _current_year() -> int:
    from datetime import date

    return date.today().year


def _runtime_matches(ticks, runtime_min, runtime_max) -> bool:
    if not ticks:
        return False
    minutes = ticks / _TICKS_PER_MINUTE
    if runtime_min and minutes < runtime_min:
        return False
    if runtime_max and minutes > runtime_max:
        return False
    return True
