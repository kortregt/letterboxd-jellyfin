import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from letterboxdpy.user import User
from letterboxdpy.movie import Movie
from config import Config
from app.cache import TTLCache

cache = TTLCache(Config.CACHE_TTL_SECONDS)


def fetch_tmdb_ids(slugs: list[str]) -> dict[str, str]:
    """Fetch TMDB IDs for a list of slugs in parallel. Returns {slug: tmdb_id}."""
    if not slugs:
        return {}
    result = {}
    uncached = []
    for slug in slugs:
        cached_id = cache.get(f"tmdb:{slug}")
        if cached_id is not None:
            result[slug] = cached_id
        else:
            uncached.append(slug)

    def _fetch_one(slug: str) -> tuple[str, str | None]:
        try:
            movie = Movie(slug)
            link = movie.tmdb_link
            if link:
                match = re.search(r"/movie/(\d+)", link)
                if match:
                    return slug, match.group(1)
        except Exception:
            pass
        return slug, None

    if uncached:
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_fetch_one, s) for s in uncached]
            for future in as_completed(futures):
                slug, tmdb_id = future.result()
                if tmdb_id:
                    result[slug] = tmdb_id
                    cache.set(f"tmdb:{slug}", tmdb_id)

    return result


def get_watchlist(username: str) -> list[dict]:
    """Fetch a user's Letterboxd watchlist. Results are cached."""
    cached = cache.get(f"watchlist:{username}")
    if cached is not None:
        return cached

    user = User(username)
    raw = user.get_watchlist_movies()

    movies = []
    for film_id, data in raw.items():
        movies.append(
            {
                "name": data.get("name", ""),
                "year": data.get("year"),
                "slug": data.get("slug", ""),
                "url": data.get("url", ""),
                "letterboxd_id": str(film_id),
            }
        )

    cache.set(f"watchlist:{username}", movies)
    return movies


def get_all_watchlists() -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Fetch watchlists for all configured friends in parallel. Returns (watchlists, errors)."""
    results = {}
    errors = {}
    friends = Config.LETTERBOXD_FRIENDS
    with ThreadPoolExecutor(max_workers=len(friends)) as executor:
        futures = {executor.submit(get_watchlist, u): u for u in friends}
        for future in as_completed(futures):
            username = futures[future]
            try:
                results[username] = future.result()
            except Exception as e:
                errors[username] = str(e)
                results[username] = []
    return results, errors
