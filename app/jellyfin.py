import random
import requests
from config import Config
from app.cache import TTLCache

cache = TTLCache(Config.CACHE_TTL_SECONDS)


class JellyfinClient:
    def __init__(self):
        self.server_url = Config.JELLYFIN_URL
        self.api_key = Config.JELLYFIN_API_KEY
        self.headers = {"X-Emby-Token": self.api_key}
        self._user_id = Config.JELLYFIN_USER_ID or None

    def _get_user_id(self) -> str:
        if self._user_id:
            return self._user_id
        response = requests.get(
            f"{self.server_url}/Users", headers=self.headers
        )
        response.raise_for_status()
        users = response.json()
        if not users:
            raise ValueError("No users found on Jellyfin server")
        self._user_id = users[0]["Id"]
        return self._user_id

    def get_movies(self, filters: dict | None = None) -> list[dict]:
        user_id = self._get_user_id()
        params = {
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "Fields": "Overview,Genres,RunTimeTicks,ProductionYear,ProviderIds",
        }
        if filters:
            if filters.get("genres"):
                params["Genres"] = ",".join(filters["genres"])
            if filters.get("year_min"):
                year_min = filters["year_min"]
                year_max = filters.get("year_max", year_min)
                params["Years"] = ",".join(
                    str(y) for y in range(int(year_min), int(year_max) + 1)
                )
            elif filters.get("year_max"):
                params["MaxProductionYear"] = str(filters["year_max"])

        response = requests.get(
            f"{self.server_url}/Users/{user_id}/Items",
            params=params,
            headers=self.headers,
        )
        response.raise_for_status()
        movies = response.json().get("Items", [])

        if filters:
            runtime_min = filters.get("runtime_min")
            runtime_max = filters.get("runtime_max")
            if runtime_min or runtime_max:
                movies = [
                    m
                    for m in movies
                    if m.get("RunTimeTicks")
                    and (
                        not runtime_min
                        or m["RunTimeTicks"] / 600_000_000 >= runtime_min
                    )
                    and (
                        not runtime_max
                        or m["RunTimeTicks"] / 600_000_000 <= runtime_max
                    )
                ]
        return movies

    def get_random_movie(self, filters: dict | None = None) -> dict | None:
        movies = self.get_movies(filters)
        if not movies:
            return None
        return self._format_movie(random.choice(movies))

    def get_all_movies_for_matching(self) -> list[dict]:
        cached = cache.get("jellyfin:movies")
        if cached is not None:
            return cached

        user_id = self._get_user_id()
        params = {
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "Fields": "ProductionYear,ProviderIds",
        }
        response = requests.get(
            f"{self.server_url}/Users/{user_id}/Items",
            params=params,
            headers=self.headers,
        )
        response.raise_for_status()
        items = response.json().get("Items", [])

        movies = []
        for item in items:
            provider_ids = item.get("ProviderIds", {})
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
        return movies

    def get_all_genres(self) -> list[str]:
        user_id = self._get_user_id()
        response = requests.get(
            f"{self.server_url}/Genres",
            params={"IncludeItemTypes": "Movie", "UserId": user_id},
            headers=self.headers,
        )
        response.raise_for_status()
        return [g["Name"] for g in response.json().get("Items", [])]

    def _format_movie(self, movie: dict) -> dict:
        runtime = None
        if movie.get("RunTimeTicks"):
            runtime = round(movie["RunTimeTicks"] / 600_000_000)
        return {
            "id": movie.get("Id"),
            "name": movie.get("Name"),
            "year": movie.get("ProductionYear"),
            "runtime": runtime,
            "genres": movie.get("Genres", []),
            "overview": movie.get("Overview", ""),
            "image_url": self._get_image_url(movie.get("Id")),
        }

    def _get_image_url(self, item_id: str | None) -> str | None:
        if not item_id:
            return None
        return f"{self.server_url}/Items/{item_id}/Images/Primary"
