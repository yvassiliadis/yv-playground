# Run: uv run uvicorn api:app --reload --port 8000
# Then open http://localhost:8000

import logging
import os

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from src import advisor_log, demo, portfolios, rebalance
from src import config as exclusions
from src.advisor import ask_committee
from src.enrichment import get_current_prices, get_live_quote
from src.models import TrackedPortfolio
from src.performance import portfolio_vs_benchmarks, tracked_portfolios_performance
from src.runner import load_all_runs, load_latest_run, run_committee

logger = logging.getLogger(__name__)

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
    run = await run_committee(ac, oc, gc, investment_amount=exclusions.INVESTMENT_AMOUNT)
    return run.model_dump(mode="json")


@app.get("/api/performance")
async def get_performance(tickers: str, weights: str):
    if not tickers or not weights:
        raise HTTPException(status_code=400, detail="No portfolio holdings to analyze")
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


@app.get("/api/quote/{ticker}")
async def get_quote(ticker: str):
    try:
        return await get_live_quote(ticker.upper())
    except Exception:
        logger.warning("Failed to fetch live quote for %s", ticker, exc_info=True)
        return {"current_price": None, "mean_upside_pct": None, "median_upside_pct": None}


@app.get("/api/settings")
async def get_settings():
    return {
        "excluded_tickers": sorted(exclusions.EXCLUDED_TICKERS),
        "excluded_sectors": sorted(exclusions.EXCLUDED_SECTORS),
        "investment_amount": exclusions.INVESTMENT_AMOUNT,
        "tax_rate": exclusions.TAX_RATE,
        "min_trade": exclusions.MIN_TRADE,
        "rebalance_portfolio": exclusions.REBALANCE_PORTFOLIO,
    }


class SettingsUpdate(BaseModel):
    # strict=True on the numeric fields: Pydantic v2's default lax mode treats
    # bool as an int subtype and silently coerces True/False into 1.0/0.0,
    # which would let a malformed `true` sail through as a valid amount.
    investment_amount: float | None = Field(default=None, strict=True)
    tax_rate: float | None = Field(default=None, strict=True)
    min_trade: float | None = Field(default=None, strict=True)
    rebalance_portfolio: str | None = None
    excluded_tickers: list[str] | None = None
    excluded_sectors: list[str] | None = None


@app.put("/api/settings")
async def update_settings(payload: SettingsUpdate):
    if payload.investment_amount is not None and not payload.investment_amount > 0:
        raise HTTPException(status_code=400, detail="investment_amount must be > 0")
    if payload.tax_rate is not None and not (0 <= payload.tax_rate < 1):
        raise HTTPException(status_code=400, detail="tax_rate must be in [0, 1)")
    if payload.min_trade is not None and not payload.min_trade >= 0:
        raise HTTPException(status_code=400, detail="min_trade must be >= 0")

    if payload.excluded_tickers is not None:
        exclusions.EXCLUDED_TICKERS.clear()
        exclusions.EXCLUDED_TICKERS.update(payload.excluded_tickers)
    if payload.excluded_sectors is not None:
        exclusions.EXCLUDED_SECTORS.clear()
        exclusions.EXCLUDED_SECTORS.update(payload.excluded_sectors)
    if payload.investment_amount is not None:
        exclusions.INVESTMENT_AMOUNT = payload.investment_amount
    if payload.tax_rate is not None:
        exclusions.TAX_RATE = payload.tax_rate
    if payload.min_trade is not None:
        exclusions.MIN_TRADE = payload.min_trade
    # rebalance_portfolio is intentionally checked via model_fields_set (not a
    # plain None check): the Settings UI sends an explicit `null` to clear the
    # selection back to "no rebalance portfolio", which must still apply.
    if "rebalance_portfolio" in payload.model_fields_set:
        exclusions.REBALANCE_PORTFOLIO = payload.rebalance_portfolio
    exclusions.save()
    return {"ok": True}


@app.get("/api/portfolios")
async def get_portfolios():
    return await portfolios.get_enriched_portfolios()


@app.put("/api/portfolios")
async def save_portfolios(payload: list[TrackedPortfolio]):
    portfolios.save(payload)
    return {"ok": True}


@app.delete("/api/portfolios/{name}")
async def delete_portfolio(name: str):
    tracked = portfolios.load()
    updated = [p for p in tracked if p.name != name]
    if len(updated) == len(tracked):
        raise HTTPException(status_code=404, detail=f"Portfolio '{name}' not found")
    portfolios.save(updated)
    return {"ok": True}


@app.post("/api/portfolios/import")
async def import_portfolio(name: str = Form(...), file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename or ""
    try:
        if filename.endswith(".xlsx"):
            positions = portfolios.parse_excel(content)
        else:
            positions = portfolios.parse_csv(content.decode("utf-8-sig"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {e}")

    if not positions:
        raise HTTPException(status_code=400, detail="No valid positions found in file")

    tracked = portfolios.load()
    tracked = [p for p in tracked if p.name != name]
    tracked.append(TrackedPortfolio(name=name, positions=positions))
    portfolios.save(tracked)
    return {"name": name, "count": len(positions)}


@app.get("/api/portfolios/performance")
async def get_portfolios_performance():
    tracked = portfolios.load()
    latest = load_latest_run()
    committee = None
    if latest:
        committee = {
            "tickers": [h.ticker for h in latest.portfolio],
            "weights": [h.weight for h in latest.portfolio],
        }
    try:
        data = tracked_portfolios_performance(tracked, committee=committee)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return data


@app.get("/api/rebalance")
async def get_rebalance(portfolio: str):
    latest = load_latest_run()
    if latest is None:
        raise HTTPException(status_code=404, detail="No committee run yet")

    tracked = portfolios.load()
    match = next((p for p in tracked if p.name == portfolio), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Portfolio '{portfolio}' not found")

    if exclusions.INVESTMENT_AMOUNT <= 0:
        raise HTTPException(status_code=400, detail="investment_amount must be > 0")

    holdings = match.positions
    targets = latest.portfolio
    all_tickers = list({pos.ticker for pos in holdings} | {tgt.ticker for tgt in targets})
    prices = await get_current_prices(all_tickers)

    plan = rebalance.plan_rebalance(
        holdings,
        targets,
        prices,
        exclusions.INVESTMENT_AMOUNT,
        exclusions.TAX_RATE,
        exclusions.MIN_TRADE,
    )
    if exclusions.INVESTMENT_AMOUNT > plan.current_total:
        raise HTTPException(
            status_code=400,
            detail=(
                f"investment_amount (${exclusions.INVESTMENT_AMOUNT:,.2f}) exceeds "
                f"portfolio's current value (${plan.current_total:,.2f})"
            ),
        )
    return plan.model_dump(mode="json")
