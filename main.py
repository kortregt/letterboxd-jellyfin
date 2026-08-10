import logging
import socket
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.cache import close_db, init_db, prune
from app.jellyfin import JellyfinError
from app.routes import router
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("moviepicker")

problems = Config.errors()
if problems:
    # A traceback here is noise: the reader is looking at a misconfigured .env,
    # not a bug. Print something they can act on and stop.
    print("Cannot start — check your .env file:\n", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print("\nSee .env.example for the full list of settings.", file=sys.stderr)
    raise SystemExit(1)

# letterboxdpy makes its own HTTP calls without a timeout, so a hung connection
# would pin a worker thread indefinitely. This is the only way to bound it.
socket.setdefaulttimeout(Config.HTTP_TIMEOUT_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    removed = prune()
    if removed:
        log.info("Pruned %d expired cache entries", removed)
    log.info(
        "Watching %d Letterboxd account(s); overlap threshold is %d",
        len(Config.LETTERBOXD_FRIENDS),
        Config.OVERLAP_MIN_FRIENDS,
    )
    yield
    close_db()


class FingerprintedStatic(StaticFiles):
    """Static files addressed by a content fingerprint, so they cache hard.

    Starlette otherwise sends no Cache-Control and browsers invent their own
    freshness window. Because every URL carries a ?v= that changes with the
    bytes, a long immutable cache is both safe and what we want.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault(
            "Cache-Control", "public, max-age=31536000, immutable"
        )
        return response


app = FastAPI(title="Letterboxd + Jellyfin Movie Picker", lifespan=lifespan)
app.mount("/static", FingerprintedStatic(directory="static"), name="static")
app.include_router(router)


@app.exception_handler(JellyfinError)
async def jellyfin_error_handler(request: Request, exc: JellyfinError):
    log.warning("Jellyfin unavailable: %s", exc)
    return JSONResponse({"error": str(exc)}, status_code=503)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        {"error": "Something went wrong. Check the server logs for details."},
        status_code=500,
    )
