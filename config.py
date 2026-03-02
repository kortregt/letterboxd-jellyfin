import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    JELLYFIN_URL = os.getenv("JELLYFIN_URL", "").rstrip("/")
    JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
    JELLYFIN_USER_ID = os.getenv("JELLYFIN_USER_ID", "")
    LETTERBOXD_FRIENDS = [
        u.strip()
        for u in os.getenv("LETTERBOXD_FRIENDS", "").split(",")
        if u.strip()
    ]
    CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
    LETTERBOXD_NICKNAMES = {}
    for pair in os.getenv("LETTERBOXD_NICKNAMES", "").split(","):
        if "=" in pair:
            uname, nick = pair.split("=", 1)
            LETTERBOXD_NICKNAMES[uname.strip()] = nick.strip()

    @staticmethod
    def validate():
        if not Config.JELLYFIN_URL:
            raise ValueError("JELLYFIN_URL not set in .env")
        if not Config.JELLYFIN_API_KEY:
            raise ValueError("JELLYFIN_API_KEY not set in .env")
        if not Config.LETTERBOXD_FRIENDS:
            raise ValueError("LETTERBOXD_FRIENDS not set in .env")
