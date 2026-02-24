from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from config import Config
from app.routes import router

Config.validate()

app = FastAPI(title="Letterboxd + Jellyfin Movie Picker")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(router)
