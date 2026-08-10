"""Match Letterboxd watchlist entries against the Jellyfin library.

Matching is done in cheap-to-expensive passes. Title-and-year covers the large
majority; accent folding and a year tolerance mop up metadata disagreements for
free; only what survives all of that is worth spending an HTTP request on to
resolve a TMDB id.
"""

import re
import unicodedata

from app.letterboxd import fetch_tmdb_ids
from config import Config

# Articles, in the languages Letterboxd titles actually show up in.
_ARTICLE_WORDS = (
    "the|a|an|le|la|les|el|los|las|il|lo|gli|der|die|das|den|det|de|het|een"
)
_ARTICLES = re.compile(rf"^({_ARTICLE_WORDS})\s+")
# Library scrapers often file "The Thing" as "Thing, The" so it sorts sensibly.
_TRAILING_ARTICLE = re.compile(rf"^(.+),\s*({_ARTICLE_WORDS})$")


def normalize_title(title: str) -> str:
    """Fold a title down to a comparable key.

    Accents are stripped because Jellyfin and Letterboxd disagree about them
    constantly — "Léon" on one side and "Leon" on the other used to be reported
    as two different films.
    """
    if not title:
        return ""
    decomposed = unicodedata.normalize("NFKD", title)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    folded = without_accents.lower().strip()
    # Undo sort-friendly filing before punctuation is discarded, otherwise
    # "Thing, The" and "The Thing" stay two different keys forever.
    moved = _TRAILING_ARTICLE.match(folded)
    if moved:
        folded = f"{moved.group(2)} {moved.group(1)}"
    folded = folded.replace("&", " and ")
    folded = re.sub(r"[^\w\s]", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def strip_article(normalized: str) -> str:
    """Drop a leading article: 'the thing' -> 'thing'."""
    return _ARTICLES.sub("", normalized, count=1).strip()


def _years_match(left, right, tolerance: int) -> bool:
    if left is None or right is None:
        # One side missing a year is weak evidence, not contradiction.
        return True
    try:
        return abs(int(left) - int(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _best_candidate(candidates: list[dict], year, tolerance: int) -> dict | None:
    """Pick the closest year among candidates sharing a title."""
    viable = [c for c in candidates if _years_match(c.get("year"), year, tolerance)]
    if not viable:
        return None
    if year is None:
        # Ambiguous without a year — only trust it when there is one option.
        return viable[0] if len(viable) == 1 else None
    def distance(candidate):
        candidate_year = candidate.get("year")
        return abs(int(candidate_year) - int(year)) if candidate_year else tolerance + 1
    return min(viable, key=distance)


class _JellyfinIndex:
    def __init__(self, movies: list[dict]):
        self.by_title: dict[str, list[dict]] = {}
        self.by_stripped: dict[str, list[dict]] = {}
        self.by_tmdb: dict[str, dict] = {}
        for movie in movies:
            normalized = normalize_title(movie.get("name", ""))
            if not normalized:
                continue
            self.by_title.setdefault(normalized, []).append(movie)
            stripped = strip_article(normalized)
            if stripped and stripped != normalized:
                self.by_stripped.setdefault(stripped, []).append(movie)
            tmdb_id = movie.get("tmdb_id")
            if tmdb_id:
                self.by_tmdb[str(tmdb_id)] = movie

    def lookup(self, normalized: str, year, tolerance: int) -> dict | None:
        match = _best_candidate(self.by_title.get(normalized, []), year, tolerance)
        if match:
            return match
        stripped = strip_article(normalized)
        pool = self.by_stripped.get(stripped, []) + self.by_title.get(stripped, [])
        return _best_candidate(pool, year, tolerance) if pool else None

    def has_title(self, normalized: str) -> bool:
        return normalized in self.by_title or strip_article(normalized) in self.by_stripped


def match_all(
    watchlists: dict[str, list[dict]],
    jellyfin_movies: list[dict],
    *,
    min_friends: int | None = None,
    year_tolerance: int | None = None,
    tmdb_budget: int | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Return (overlap, missing, stats).

    - overlap: films on at least `min_friends` watchlists, flagged for availability
    - missing: films on any watchlist that are not in the Jellyfin library
    """
    min_friends = Config.OVERLAP_MIN_FRIENDS if min_friends is None else min_friends
    tolerance = Config.YEAR_TOLERANCE if year_tolerance is None else year_tolerance
    budget = Config.TMDB_RESOLVE_BUDGET if tmdb_budget is None else tmdb_budget

    index = _JellyfinIndex(jellyfin_movies)

    # Collapse every friend's list into one entry per film.
    aggregated: dict[tuple, dict] = {}
    for username, movies in watchlists.items():
        for movie in movies:
            normalized = normalize_title(movie.get("name", ""))
            key = (normalized, movie.get("year"))
            entry = aggregated.get(key)
            if entry is None:
                entry = {"movie": movie, "normalized": normalized, "friends": set()}
                aggregated[key] = entry
            entry["friends"].add(username)

    # Pass 1-3: title, article-insensitive title, year tolerance. All free.
    unmatched: list[dict] = []
    for entry in aggregated.values():
        match = index.lookup(entry["normalized"], entry["movie"].get("year"), tolerance)
        if match:
            entry["jf_match"] = match
        else:
            unmatched.append(entry)

    # Pass 4: resolve TMDB ids for a bounded slice of what is left. Films whose
    # title already appears on the server are tried first — those are almost
    # always a metadata disagreement rather than a genuinely absent film — then
    # the remaining budget goes to the rest, which is how a film filed under a
    # different title gets found at all. Ids cache permanently, so successive
    # refreshes work through the backlog instead of re-scraping.
    resolved_count = 0
    if unmatched and index.by_tmdb and budget:
        likely, rest = [], []
        for entry in unmatched:
            (likely if index.has_title(entry["normalized"]) else rest).append(entry)
        selected = [e for e in likely + rest if e["movie"].get("slug")]

        # Everything unmatched is offered, in priority order; the budget limits
        # how many uncached lookups actually happen. Capping the list here
        # instead would hand the whole allowance to the same already-resolved
        # films every sync, and the backlog would never shrink.
        tmdb_map = fetch_tmdb_ids([e["movie"]["slug"] for e in selected], budget)
        for entry in selected:
            tmdb_id = tmdb_map.get(entry["movie"]["slug"])
            if not tmdb_id:
                continue
            match = index.by_tmdb.get(str(tmdb_id))
            if match:
                entry["jf_match"] = match
                resolved_count += 1

    overlap, missing = [], []
    for entry in aggregated.values():
        match = entry.get("jf_match")
        movie = entry["movie"]
        friends = sorted(entry["friends"])
        base = {
            "name": movie.get("name", ""),
            "year": movie.get("year"),
            "url": movie.get("url", ""),
            "slug": movie.get("slug", ""),
            "wanted_by": friends,
        }
        if len(friends) >= min_friends:
            overlap.append(
                {
                    **base,
                    "on_jellyfin": match is not None,
                    "jellyfin_id": match["jellyfin_id"] if match else None,
                }
            )
        if match is None:
            missing.append(base)

    def sort_key(item):
        return (-len(item["wanted_by"]), item["name"].lower())

    overlap.sort(key=sort_key)
    missing.sort(key=sort_key)

    stats = {
        "total_films": len(aggregated),
        "on_jellyfin": sum(1 for e in aggregated.values() if e.get("jf_match")),
        "tmdb_resolved": resolved_count,
        "unresolved": max(0, len(unmatched) - resolved_count),
        "min_friends": min_friends,
    }
    return overlap, missing, stats
