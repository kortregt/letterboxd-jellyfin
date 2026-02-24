import re
import random
from app.letterboxd import fetch_tmdb_ids


def normalize_title(title: str) -> str:
    title = title.lower().strip()
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title


def match_all(
    watchlists: dict[str, list[dict]],
    jellyfin_movies: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Single-pass matching. Returns (overlap, missing).
    - overlap: movies on 2+ friends' watchlists, annotated with on_jellyfin
    - missing: movies on any watchlist but NOT on Jellyfin
    """
    # Build Jellyfin indexes once
    jf_by_title_year = {}
    jf_by_tmdb = {}
    jf_titles: set[str] = set()  # all normalized titles on Jellyfin
    for m in jellyfin_movies:
        norm = normalize_title(m["name"])
        key = (norm, m.get("year"))
        jf_by_title_year[key] = m
        jf_titles.add(norm)
        if m.get("tmdb_id"):
            jf_by_tmdb[str(m["tmdb_id"])] = m

    # Aggregate all watchlist movies once
    aggregated: dict[tuple, dict] = {}
    for username, movies in watchlists.items():
        for movie in movies:
            key = (normalize_title(movie["name"]), movie.get("year"))
            if key not in aggregated:
                aggregated[key] = {"movie": movie, "friends": set()}
            aggregated[key]["friends"].add(username)

    # Pass 1: title+year match
    unmatched_entries: list[dict] = []

    for key, data in aggregated.items():
        jf_match = jf_by_title_year.get(key)
        if jf_match:
            data["jf_match"] = jf_match
        else:
            unmatched_entries.append(data)

    # Pass 2: TMDB resolution ONLY for movies whose title exists on
    # Jellyfin but with a different year (probable metadata mismatch).
    # This keeps the set tiny (~30) instead of all unmatched (~1000+).
    if unmatched_entries and jf_by_tmdb:
        candidates = [
            d for d in unmatched_entries
            if normalize_title(d["movie"]["name"]) in jf_titles
        ]
        if candidates:
            slugs = [d["movie"]["slug"] for d in candidates if d["movie"].get("slug")]
            tmdb_map = fetch_tmdb_ids(slugs)
            for data in candidates:
                slug = data["movie"].get("slug", "")
                tmdb_id = tmdb_map.get(slug)
                if tmdb_id:
                    jf_match = jf_by_tmdb.get(str(tmdb_id))
                    if jf_match:
                        data["jf_match"] = jf_match

    # Build results
    overlap = []
    missing = []

    for data in aggregated.values():
        jf_match = data.get("jf_match")
        friends = data["friends"]
        movie = data["movie"]

        if len(friends) >= 2:
            overlap.append({
                "name": movie["name"],
                "year": movie["year"],
                "url": movie.get("url", ""),
                "slug": movie.get("slug", ""),
                "wanted_by": sorted(friends),
                "on_jellyfin": jf_match is not None,
                "jellyfin_id": jf_match["jellyfin_id"] if jf_match else None,
            })

        if not jf_match:
            missing.append({
                "name": movie["name"],
                "year": movie["year"],
                "url": movie.get("url", ""),
                "slug": movie.get("slug", ""),
                "wanted_by": sorted(friends),
            })

    overlap.sort(key=lambda x: len(x["wanted_by"]), reverse=True)
    missing.sort(key=lambda x: len(x["wanted_by"]), reverse=True)

    return overlap, missing
