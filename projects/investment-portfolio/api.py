# Run: uv run uvicorn api:app --reload --port 8000
# Then open http://localhost:8000

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from openai import AsyncOpenAI
import anthropic

from src import advisor_log
from src import config as exclusions
from src import demo
from src.advisor import ask_committee
from src.performance import portfolio_vs_benchmarks
from src.runner import load_all_runs, load_latest_run, run_committee

load_dotenv()
exclusions.load()
demo.ensure_demo_data()

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")


def _clients():
    if demo.is_demo_mode():
        raise HTTPException(
            status_code=503,
            detail=f"Demo mode: add {', '.join(demo._REQUIRED_KEYS)} to .env to enable live AI runs",
        )
    return (
        anthropic.AsyncAnthropic(),
        AsyncOpenAI(),
        genai.Client(api_key=os.environ["GOOGLE_API_KEY"]),
    )


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")


@app.get("/api/runs")
async def get_runs():
    runs = load_all_runs()
    return [r.model_dump(mode="json") for r in runs]


@app.get("/api/runs/latest")
async def get_latest_run():
    run = load_latest_run()
    if not run:
        raise HTTPException(status_code=404, detail="No runs yet")
    return run.model_dump(mode="json")


@app.post("/api/runs")
async def trigger_run():
    ac, oc, gc = _clients()
    run = await run_committee(ac, oc, gc)
    return run.model_dump(mode="json")


@app.get("/api/performance")
async def get_performance(tickers: str, weights: str):
    try:
        ticker_list = tickers.split(",")
        weight_list = [float(w) for w in weights.split(",")]
        data = portfolio_vs_benchmarks(ticker_list, weight_list)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return data


@app.post("/api/advisor")
async def get_advisor_opinion(payload: dict):
    ticker = payload.get("ticker", "").upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker required")
    latest = load_latest_run()
    portfolio = latest.portfolio if latest else []
    ac, oc, gc = _clients()
    try:
        advice = await ask_committee(ticker, ac, oc, gc, portfolio)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    advisor_log.append(advice)
    return advice.model_dump(mode="json")


@app.get("/api/advisor/log")
async def get_advisor_log():
    return advisor_log.load()


@app.get("/api/settings")
async def get_settings():
    return {
        "excluded_tickers": sorted(exclusions.EXCLUDED_TICKERS),
        "excluded_sectors": sorted(exclusions.EXCLUDED_SECTORS),
    }


@app.put("/api/settings")
async def update_settings(payload: dict):
    if "excluded_tickers" in payload:
        exclusions.EXCLUDED_TICKERS.clear()
        exclusions.EXCLUDED_TICKERS.update(payload["excluded_tickers"])
    if "excluded_sectors" in payload:
        exclusions.EXCLUDED_SECTORS.clear()
        exclusions.EXCLUDED_SECTORS.update(payload["excluded_sectors"])
    exclusions.save()
    return {"ok": True}
