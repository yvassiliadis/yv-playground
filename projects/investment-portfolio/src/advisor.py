import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yfinance as yf
from google import genai
from openai import AsyncOpenAI

from .committee import claude_member, gemini_member, gpt_member
from .models import AdvisorResponse, PortfolioHolding

logger = logging.getLogger(__name__)

_ADVISOR_CACHE_PATH = Path(__file__).parent.parent / "data" / "advisor_cache.json"
_ADVISOR_CACHE_TTL_SECONDS = 4 * 3600


def _load_cache() -> dict:
    if not _ADVISOR_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_ADVISOR_CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _ADVISOR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ADVISOR_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass


def _get_cached(ticker: str) -> dict | None:
    entry = _load_cache().get(ticker)
    if not entry:
        return None
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(entry["cached_at"])).total_seconds()
    return entry if age < _ADVISOR_CACHE_TTL_SECONDS else None


def _set_cached(ticker: str, data: dict) -> None:
    cache = _load_cache()
    cache[ticker] = {"cached_at": datetime.now(timezone.utc).isoformat(), **data}
    _save_cache(cache)


def _resolve_ticker(query: str) -> str:
    try:
        equities = [q for q in yf.Search(query).quotes if q.get("quoteType") == "EQUITY"]
        if equities:
            return equities[0]["symbol"]
    except Exception:
        pass
    return query.upper().strip()


def _fetch_ticker_info(ticker: str) -> tuple[str, dict, str | None]:
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return "", {}, None

    def fmt_pct(v) -> str | None:
        try:
            f = float(v)
            return f"{f:.1%}" if not math.isnan(f) else None
        except (TypeError, ValueError):
            return None

    def fmt_float(v, decimals=1) -> str | None:
        try:
            f = float(v)
            return f"{f:.{decimals}f}" if not math.isnan(f) else None
        except (TypeError, ValueError):
            return None

    def fmt_billions(v) -> str | None:
        try:
            f = float(v)
            return f"${f / 1e9:.1f}B" if not math.isnan(f) else None
        except (TypeError, ValueError):
            return None

    fields = {
        "Market cap": fmt_billions(info.get("marketCap")),
        "Revenue growth (YoY)": fmt_pct(info.get("revenueGrowth")),
        "Gross margin": fmt_pct(info.get("grossMargins")),
        "ROE": fmt_pct(info.get("returnOnEquity")),
        "D/E ratio": fmt_float(info.get("debtToEquity")),
        "Trailing P/E": fmt_float(info.get("trailingPE")),
        "Forward P/E": fmt_float(info.get("forwardPE")),
        "FCF": fmt_billions(info.get("freeCashflow")),
    }
    lines = [f"{k}: {v}" for k, v in fields.items() if v is not None]
    fundamentals = "Current fundamentals:\n" + "\n".join(lines) if lines else ""

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    mean_t = info.get("targetMeanPrice")
    median_t = info.get("targetMedianPrice")
    upside = {
        "mean_upside_pct": round((mean_t - price) / price * 100, 1) if price and mean_t else None,
        "median_upside_pct": round((median_t - price) / price * 100, 1) if price and median_t else None,
    }

    company_name = info.get("longName") or info.get("shortName")
    return fundamentals, upside, company_name


def _format_portfolio_context(holdings: list[PortfolioHolding]) -> str:
    if not holdings:
        return ""
    lines = ["Current portfolio allocation:"]
    for h in sorted(holdings, key=lambda x: -x.weight):
        lines.append(f"  {h.ticker} ({h.conviction}): {h.weight:.1f}%")
    lines.append(f"  Total positions: {len(holdings)}")
    lines.append(
        "Based on the above, suggest an appropriate allocation percentage for the stock being evaluated."
    )
    return "\n".join(lines)


def _avg_allocation(results: list[dict]) -> float | None:
    values = [
        float(r["suggested_allocation_pct"])
        for r in results
        if r.get("suggested_allocation_pct") is not None
    ]
    return round(sum(values) / len(values), 1) if values else None


async def ask_committee(
    ticker: str,
    anthropic_client: anthropic.AsyncAnthropic,
    openai_client: AsyncOpenAI,
    gemini_client: genai.Client,
    current_portfolio: list[PortfolioHolding],
) -> AdvisorResponse:
    ticker = await asyncio.to_thread(_resolve_ticker, ticker.strip())
    already_in = ticker in {h.ticker.upper() for h in current_portfolio}

    cached = _get_cached(ticker)
    yf_company_name: str | None = None
    members_needed = [
        m for m in ("claude", "gpt", "gemini")
        if cached is None or cached.get(m) is None
    ]

    if not members_needed and cached:
        # Full cache hit
        claude_result = cached["claude"] or {}
        gpt_result = cached["gpt"] or {}
        gemini_result = cached["gemini"] or {}
        upside = cached.get("upside", {})
    else:
        (fundamentals, upside, yf_company_name), portfolio_context = await asyncio.gather(
            asyncio.to_thread(_fetch_ticker_info, ticker),
            asyncio.to_thread(_format_portfolio_context, current_portfolio),
        )

        task_map = {}
        if "claude" in members_needed:
            task_map["claude"] = claude_member.get_stock_opinion(anthropic_client, ticker, fundamentals, portfolio_context)
        if "gpt" in members_needed:
            task_map["gpt"] = gpt_member.get_stock_opinion(openai_client, ticker, fundamentals, portfolio_context)
        if "gemini" in members_needed:
            task_map["gemini"] = gemini_member.get_stock_opinion(gemini_client, ticker, fundamentals, portfolio_context)

        gathered = await asyncio.gather(*task_map.values(), return_exceptions=True)
        new_results = {}
        for key, result in zip(task_map.keys(), gathered):
            if isinstance(result, Exception):
                logger.warning(f"{key.capitalize()} advisor failed — excluding from response", exc_info=result)
                new_results[key] = None
            else:
                new_results[key] = result

        merged = {m: (cached or {}).get(m) for m in ("claude", "gpt", "gemini")}
        merged.update(new_results)

        _set_cached(ticker, {"upside": upside, "yf_company_name": yf_company_name, **merged})

        claude_result = merged["claude"] or {}
        gpt_result = merged["gpt"] or {}
        gemini_result = merged["gemini"] or {}

    if not any([claude_result, gpt_result, gemini_result]):
        raise RuntimeError("All committee members failed — cannot generate advice")

    company_name = (
        (cached or {}).get("yf_company_name")
        or yf_company_name
        or claude_result.get("company_name")
        or gpt_result.get("company_name")
        or gemini_result.get("company_name")
        or ticker
    )

    available = [r for r in [claude_result, gpt_result, gemini_result] if r]
    fits = all(r.get("fits_philosophy", True) for r in available)

    if already_in:
        final_rec = "already in portfolio"
    elif len(available) == 1:
        final_rec = "not enough opinions"
    else:
        buy_votes = sum(1 for r in available if r.get("recommendation") == "buy")
        match (len(available), buy_votes):
            case (3, 3): final_rec = "strong buy"
            case (_, 2): final_rec = "buy"
            case (_, 1): final_rec = "watch"
            case _:      final_rec = "pass"

    return AdvisorResponse(
        ticker=ticker,
        company_name=company_name,
        recommendation=final_rec,
        claude_take=claude_result.get("take", ""),
        gpt_take=gpt_result.get("take", ""),
        gemini_take=gemini_result.get("take", ""),
        claude_rec=claude_result.get("recommendation"),
        gpt_rec=gpt_result.get("recommendation"),
        gemini_rec=gemini_result.get("recommendation"),
        fits_philosophy=fits,
        suggested_allocation_pct=_avg_allocation(available),
        mean_upside_pct=upside.get("mean_upside_pct"),
        median_upside_pct=upside.get("median_upside_pct"),
    )
