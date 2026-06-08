from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.routers.games import router as games_router

app = FastAPI(title="Scopee's Seven")
app.include_router(games_router)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
