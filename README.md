# Letterboxd + Jellyfin Movie Picker

A self-hosted web app that combines your friends' Letterboxd watchlists with your Jellyfin media server.

## Features

- **Random Picker** — pick a random movie from your Jellyfin library with genre/year/runtime filters
- **Friend Overlap** — see which movies appear on multiple friends' watchlists, with Jellyfin availability
- **Movies to Add** — movies your friends want that aren't on your server yet

## Setup

```bash
cp .env.example .env
# Edit .env with your Jellyfin URL, API key, and friends' Letterboxd usernames
```

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

## Configuration

| Variable | Description |
|---|---|
| `JELLYFIN_URL` | Your Jellyfin server URL |
| `JELLYFIN_API_KEY` | Jellyfin API key (Admin Dashboard → API Keys) |
| `JELLYFIN_USER_ID` | Optional — auto-detects first user if blank |
| `LETTERBOXD_FRIENDS` | Comma-separated Letterboxd usernames |
| `CACHE_TTL_SECONDS` | How long to cache watchlist data (default: 3600) |
