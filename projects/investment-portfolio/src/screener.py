import asyncio
import dataclasses
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from finvizfinance.screener.financial import Financial
from finvizfinance.screener.overview import Overview

from .config import EXCLUDED_SECTORS, EXCLUDED_TICKERS

logger = logging.getLogger(__name__)

_SCREENER_CACHE_PATH = Path(__file__).parent.parent / "data" / "screener_cache.json"
_SCREENER_CACHE_TTL_SECONDS = 24 * 3600


def _load_screener_cache() -> "list[ScreenedStock] | None":
    if not _SCREENER_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_SCREENER_CACHE_PATH.read_text())
        cached_at = datetime.fromisoformat(data["cached_at"])
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > _SCREENER_CACHE_TTL_SECONDS:
            return None
        stocks = [ScreenedStock(**s) for s in data["stocks"]]
        logger.info(
            "Screener cache hit: %d stocks (%.0fh old)", len(stocks), age / 3600
        )
        return stocks
    except Exception:
        logger.debug("Screener cache invalid, re-screening", exc_info=True)
        return None


def _save_screener_cache(stocks: "list[ScreenedStock]") -> None:
    try:
        _SCREENER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SCREENER_CACHE_PATH.write_text(
            json.dumps(
                {
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "stocks": [dataclasses.asdict(s) for s in stocks],
                },
                indent=2,
            )
        )
    except Exception:
        logger.warning("Failed to save screener cache", exc_info=True)


# Finviz bucket values — D/E "Under 1" (1.0x) is the closest to our 1.5x threshold.
FINVIZ_FILTERS = {
    "Gross Margin": "Over 40%",
    "Debt/Equity": "Under 1",
    "Market Cap.": "+Mid (over $2bln)",
    "PEG": "Under 3",
}

SUGGESTIONS_ROE_MIN = 0.20
OPPORTUNITIES_ROE_FLOOR = -0.15
FCF_EBITDA_MIN = 0.80
FCF_SALES_MIN = 0.05

MAX_CONCURRENT_FCF_FETCHES = 30


@dataclass
class ScreenedStock:
    ticker: str
    company_name: str
    industry: str | None
    gross_margin: float | None
    roe: float | None
    roic: float | None
    operating_margin: float | None
    earnings_date: str | None
    tier: str  # "suggestion" | "opportunity"


def _to_float(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _run_financial_screener() -> pd.DataFrame:
    print("  [screener] Querying Finviz (financial view)...", flush=True)
    screener = Financial()
    screener.set_filter(filters_dict=FINVIZ_FILTERS)
    df = screener.screener_view(verbose=0)
    if df is None or df.empty:
        return pd.DataFrame(columns=["Ticker", "ROE", "Gross M"])
    print(f"  [screener] Finviz financial: {len(df)} tickers", flush=True)
    wanted = ["Ticker", "ROE", "Gross M", "ROIC", "Oper M", "Earnings"]
    cols = [c for c in wanted if c in df.columns]
    return df[cols]


def _run_overview_screener() -> pd.DataFrame:
    print("  [screener] Querying Finviz (overview view)...", flush=True)
    screener = Overview()
    screener.set_filter(filters_dict=FINVIZ_FILTERS)
    df = screener.screener_view(verbose=0)
    if df is None or df.empty:
        return pd.DataFrame(columns=["Ticker", "Company", "Sector"])
    wanted = ["Ticker", "Company", "Sector", "Industry"]
    cols = [c for c in wanted if c in df.columns]
    return df[cols]


def _fcf_qualifies(info: dict) -> bool:
    fcf = info.get("freeCashflow") or 0
    ebitda = info.get("ebitda")
    revenue = info.get("totalRevenue")
    conv_ok = ebitda is not None and ebitda > 0 and (fcf / ebitda) >= FCF_EBITDA_MIN
    sales_ok = revenue is not None and revenue > 0 and (fcf / revenue) >= FCF_SALES_MIN
    return conv_ok and sales_ok


async def _fetch_fcf_info(
    ticker: str, sem: asyncio.Semaphore, counter: list[int], total: int
) -> tuple[str, dict | None]:
    async with sem:
        try:
            info = await asyncio.to_thread(lambda: yf.Ticker(ticker).info)
            return ticker, info
        except Exception:
            logger.debug("Failed to fetch FCF info for %s", ticker)
            return ticker, None
        finally:
            counter[0] += 1
            print(f"  [screener] FCF check: {counter[0]}/{total}", flush=True)


async def screen_universe() -> list[ScreenedStock]:
    """Screen US equities via Finviz financial + overview views.

    yfinance is called only for stocks with slightly negative ROE (-20% to 0%)
    that need FCF qualification. All other qualification is done via Finviz.
    """
    cached = _load_screener_cache()
    if cached is not None:
        return cached

    excluded_tickers = {t.upper() for t in EXCLUDED_TICKERS}

    t0 = time.monotonic()
    financial_df, overview_df = await asyncio.gather(
        asyncio.to_thread(_run_financial_screener),
        asyncio.to_thread(_run_overview_screener),
    )
    print(f"[Finviz done] {time.monotonic() - t0:.1f}s", flush=True)

    if financial_df.empty:
        logger.warning("Finviz financial screener returned no results")
        return []

    merged = financial_df.merge(overview_df, on="Ticker", how="left")
    merged = merged[~merged["Ticker"].str.upper().isin(excluded_tickers)]
    merged = merged[~merged["Sector"].isin(EXCLUDED_SECTORS)]

    suggestions: list[ScreenedStock] = []
    needs_fcf: list[
        tuple[
            str,
            str,
            str | None,
            float | None,
            float,
            float | None,
            float | None,
            str | None,
        ]
    ] = []

    for _, row in merged.iterrows():
        ticker = str(row["Ticker"])
        roe = _to_float(row.get("ROE"))

        if roe is None:
            continue

        gm = _to_float(row.get("Gross M"))
        roic = _to_float(row.get("ROIC"))
        operating_margin = _to_float(row.get("Oper M"))
        earnings_raw = row.get("Earnings")
        earnings_date = (
            str(earnings_raw)
            if earnings_raw
            and not (isinstance(earnings_raw, float) and math.isnan(earnings_raw))
            else None
        )
        company_raw = row.get("Company")
        company = (
            str(company_raw)
            if company_raw
            and not (isinstance(company_raw, float) and math.isnan(company_raw))
            else ticker
        )
        industry_raw = row.get("Industry")
        industry = (
            str(industry_raw)
            if industry_raw
            and not (isinstance(industry_raw, float) and math.isnan(industry_raw))
            else None
        )

        if roe >= SUGGESTIONS_ROE_MIN:
            suggestions.append(
                ScreenedStock(
                    ticker=ticker,
                    company_name=company,
                    industry=industry,
                    gross_margin=gm,
                    roe=roe,
                    roic=roic,
                    operating_margin=operating_margin,
                    earnings_date=earnings_date,
                    tier="suggestion",
                )
            )
        elif OPPORTUNITIES_ROE_FLOOR < roe < 0:
            needs_fcf.append(
                (
                    ticker,
                    company,
                    industry,
                    gm,
                    roe,
                    roic,
                    operating_margin,
                    earnings_date,
                )
            )

    opportunities: list[ScreenedStock] = []
    if needs_fcf:
        print(f"  [screener] {len(needs_fcf)} FCF checks (−15%<ROE<0%)...", flush=True)
        t1 = time.monotonic()
        sem = asyncio.Semaphore(MAX_CONCURRENT_FCF_FETCHES)
        counter = [0]
        fcf_results = await asyncio.gather(
            *[_fetch_fcf_info(t, sem, counter, len(needs_fcf)) for t, *_ in needs_fcf]
        )
        fcf_map = {ticker: info for ticker, info in fcf_results}

        for (
            ticker,
            company,
            industry,
            gm,
            roe,
            roic,
            operating_margin,
            earnings_date,
        ) in needs_fcf:
            info = fcf_map.get(ticker)
            if info and _fcf_qualifies(info):
                opportunities.append(
                    ScreenedStock(
                        ticker=ticker,
                        company_name=company,
                        industry=industry,
                        gross_margin=gm,
                        roe=roe,
                        roic=roic,
                        operating_margin=operating_margin,
                        earnings_date=earnings_date,
                        tier="opportunity",
                    )
                )
        print(
            f"[Yahoo done] {time.monotonic() - t1:.1f}s — {len(opportunities)}/{len(needs_fcf)} passed FCF",
            flush=True,
        )

    logger.info(
        "Screened: %d suggestions, %d opportunities",
        len(suggestions),
        len(opportunities),
    )
    result = suggestions + opportunities
    _save_screener_cache(result)
    return result


def format_for_prompt(stocks: list[ScreenedStock]) -> str:
    """Format screened stocks as two labeled sections for member prompts."""
    if not stocks:
        return ""

    def stock_line(s: ScreenedStock) -> str:
        metrics: list[str] = []
        if s.gross_margin is not None:
            metrics.append(f"gross margin {s.gross_margin:.0%}")
        if s.roe is not None:
            metrics.append(f"ROE {s.roe:.0%}")
        if s.roic is not None:
            metrics.append(f"ROIC {s.roic:.0%}")
        if s.operating_margin is not None:
            metrics.append(f"oper margin {s.operating_margin:.0%}")
        if s.earnings_date is not None:
            metrics.append(f"earnings {s.earnings_date}")
        metric_str = ", ".join(metrics) if metrics else "metrics unavailable"
        industry_str = f" [{s.industry}]" if s.industry else ""
        return f"{s.ticker} ({s.company_name}){industry_str} — {metric_str}"

    suggestions = sorted(
        [s for s in stocks if s.tier == "suggestion"], key=lambda x: x.ticker
    )
    opportunities = sorted(
        [s for s in stocks if s.tier == "opportunity"], key=lambda x: x.ticker
    )

    lines: list[str] = []

    if suggestions:
        lines.append(
            "SUGGESTIONS — quality compounders (ROE ≥ 20%, pick primarily from here):"
        )
        lines.extend(stock_line(s) for s in suggestions)

    if opportunities:
        if lines:
            lines.append("")
        lines.append(
            "OPPORTUNITIES — turnaround/recovery plays (negative ROE but strong FCF; higher risk, use sparingly):"
        )
        lines.extend(stock_line(s) for s in opportunities)

    lines.append("")
    lines.append("Pick only from the stocks listed above.")
    return "\n".join(lines)
