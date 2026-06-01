import csv
import io
import json
from pathlib import Path

from .enrichment import get_current_prices
from .models import PortfolioPosition, TrackedPortfolio

_PORTFOLIOS_PATH = Path(__file__).parent.parent / "data" / "portfolios.json"


def load() -> list[TrackedPortfolio]:
    if not _PORTFOLIOS_PATH.exists():
        return []
    try:
        data = json.loads(_PORTFOLIOS_PATH.read_text())
        return [TrackedPortfolio.model_validate(p) for p in data]
    except Exception:
        return []


def save(tracked: list[TrackedPortfolio]) -> None:
    _PORTFOLIOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PORTFOLIOS_PATH.write_text(json.dumps([p.model_dump() for p in tracked], indent=2))


def parse_csv(content: str) -> list[PortfolioPosition]:
    # Strip BOM that Excel adds when exporting CSV
    content = content.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(content))
    positions = []
    for row in reader:
        ticker = (row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        try:
            shares = float((row.get("shares") or "").strip())
        except ValueError:
            continue
        avg_cost_str = (row.get("avg_cost") or "").strip()
        avg_cost = float(avg_cost_str) if avg_cost_str else None
        positions.append(
            PortfolioPosition(ticker=ticker, shares=shares, avg_cost=avg_cost)
        )
    return positions


def parse_excel(file_bytes: bytes) -> list[PortfolioPosition]:
    import pandas as pd

    df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
    df.columns = [str(c).lower().strip() for c in df.columns]
    positions = []
    for _, row in df.iterrows():
        ticker = str(row.get("ticker", "")).upper().strip()
        if not ticker or ticker == "NAN":
            continue
        try:
            shares = float(row["shares"])
        except (ValueError, TypeError, KeyError):
            continue
        raw_cost = row.get("avg_cost")
        try:
            avg_cost = (
                float(raw_cost)
                if raw_cost is not None and not pd.isna(raw_cost)
                else None
            )
        except (ValueError, TypeError):
            avg_cost = None
        positions.append(
            PortfolioPosition(ticker=ticker, shares=shares, avg_cost=avg_cost)
        )
    return positions


async def enrich(portfolio: TrackedPortfolio, prices: dict[str, float | None]) -> dict:
    """Returns portfolio data dict with current prices, values, weights, and returns."""
    enriched_positions = []
    total_value = 0.0
    total_cost = 0.0
    all_have_cost = True

    for pos in portfolio.positions:
        price = prices.get(pos.ticker)
        value = pos.shares * price if price is not None else None
        if value is not None:
            total_value += value
        if pos.avg_cost is not None:
            total_cost += pos.shares * pos.avg_cost
        else:
            all_have_cost = False
        enriched_positions.append(
            {
                "ticker": pos.ticker,
                "shares": pos.shares,
                "avg_cost": pos.avg_cost,
                "current_price": price,
                "total_value": round(value, 2) if value is not None else None,
            }
        )

    for ep in enriched_positions:
        ep["weight"] = (
            round(ep["total_value"] / total_value * 100, 1)
            if total_value and ep["total_value"] is not None
            else None
        )
        if ep["avg_cost"] is not None and ep["current_price"] is not None:
            ep["return_pct"] = round(
                (ep["current_price"] - ep["avg_cost"]) / ep["avg_cost"] * 100, 1
            )
        else:
            ep["return_pct"] = None

    total_return_pct = None
    if all_have_cost and total_cost > 0 and total_value > 0:
        total_return_pct = round((total_value - total_cost) / total_cost * 100, 1)

    return {
        "name": portfolio.name,
        "positions": enriched_positions,
        "total_value": round(total_value, 2) if total_value else None,
        "total_return_pct": total_return_pct,
    }


async def get_enriched_portfolios() -> list[dict]:
    """Loads portfolios, fetches current prices, and returns enriched data."""
    tracked = load()
    if not tracked:
        return []
    all_tickers = list({pos.ticker for p in tracked for pos in p.positions})
    prices = await get_current_prices(all_tickers)
    return [await enrich(p, prices) for p in tracked]
