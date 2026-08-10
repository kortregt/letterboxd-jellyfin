# Letterboxd + Jellyfin Movie Picker

A self-hosted web app that combines your friends' Letterboxd watchlists with your
Jellyfin media server.

## Features

- **Random Picker** — pick a random film from your Jellyfin library, with genre,
  year and runtime filters
- **Friend Overlap** — films several friends want to watch, flagged by whether
  they are on your server, filterable by who and by *any/all*
- **Movies to Add** — films your friends want that aren't on your server yet

## Setup

```bash
cp .env.example .env
```

Fill in your Jellyfin URL, an API key (Jellyfin → Dashboard → API Keys) and the
Letterboxd usernames you want to follow. Every other setting is optional and
documented in `.env.example`.

### Docker

```bash
docker compose up -d --build
```

### Local

```bash
uv sync
uv run uvicorn main:app --reload
```

Open `http://localhost:8000`.

## Tests

```bash
uv run pytest
```

## How matching works

Letterboxd and Jellyfin disagree about film metadata more than you would expect,
so titles are matched in passes, cheapest first:

1. Normalised title + year. Normalisation folds accents (`Léon` = `Leon`),
   expands `&`, drops punctuation, and un-inverts sort-friendly names
   (`Thing, The` = `The Thing`).
2. The same comparison ignoring a leading article, and allowing the year to be
   off by `YEAR_TOLERANCE` (default 1).
3. For whatever is left, a per-film TMDB id is scraped from Letterboxd and
   matched against Jellyfin's provider ids. This is the only step that costs an
   HTTP request per film, so it is capped at `TMDB_RESOLVE_BUDGET` per sync.
   Resolved ids are cached permanently, so successive refreshes work through the
   backlog rather than re-scraping.

Step 3 is what finds films filed under a different title — the case that
otherwise shows up as a false entry in *Movies to Add*.

## When Letterboxd is down

Letterboxd has no public API, so watchlists are scraped, and the site goes down
from time to time. Rather than showing an empty page, the app keeps serving the
last good copy for up to `STALE_MAX_AGE_SECONDS` (default a week) and displays a
banner saying how old the data is. Requests are retried with backoff, and
concurrency is kept deliberately low to avoid being rate-limited.

Failures that retrying cannot fix — a block, a missing account, a private
profile — are reported immediately instead of being retried, because waiting
does not change the answer and the user is watching a spinner meanwhile.

### "Letterboxd refused the request (HTTP 403)"

Usually this is **not** an IP ban. Letterboxd sits behind Cloudflare, which
letterboxdpy gets past by impersonating a real browser's TLS fingerprint. When
Cloudflare changes, every request 403s until the library (and its `curl_cffi`
dependency) is updated:

```bash
uv lock --upgrade-package letterboxdpy && uv sync
```

See [letterboxdpy#167](https://github.com/nmcassa/letterboxdpy/issues/167). If
that doesn't help, check whether the host is behind a VPN or proxy.

## Configuration

See `.env.example` — every variable is listed with a comment. Only
`JELLYFIN_URL`, `JELLYFIN_API_KEY` and `LETTERBOXD_FRIENDS` are required; a bad
config is reported as a readable list at startup rather than a traceback.

## Notes

Posters are proxied through this app rather than linked directly, so the browser
never needs to reach `JELLYFIN_URL` — which is usually an internal address, and
not something worth publishing to every page visitor.
