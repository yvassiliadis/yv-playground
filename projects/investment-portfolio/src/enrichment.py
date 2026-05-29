import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

from .models import Pick

_SEMAPHORE = asyncio.Semaphore(5)
_ENRICHMENT_CACHE_PATH = Path(__file__).parent.parent / "data" / "enrichment_cache.json"
_ENRICHMENT_CACHE_TTL_SECONDS = 2 * 3600


def _load_enrichment_cache() -> dict[str, dict]:
    if not _ENRICHMENT_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_ENRICHMENT_CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_enrichment_cache(cache: dict[str, dict]) -> None:
    try:
        _ENRICHMENT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ENRICHMENT_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass


async def _fetch_ticker_data(ticker: str) -> dict:
    async with _SEMAPHORE:
        info = await asyncio.to_thread(lambda: yf.Ticker(ticker).info)
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    mean_t = info.get("targetMeanPrice")
    median_t = info.get("targetMedianPrice")
    return {"current_price": price, "mean_target": mean_t, "median_target": median_t}


async def enrich_picks_with_prices(picks: list[Pick]) -> list[Pick]:
    tickers = list({p.ticker for p in picks})

    cache = _load_enrichment_cache()
    now = datetime.now(timezone.utc)

    stale = [
        t for t in tickers
        if t not in cache
        or (now - datetime.fromisoformat(cache[t]["cached_at"])).total_seconds() > _ENRICHMENT_CACHE_TTL_SECONDS
    ]

    if stale:
        results = await asyncio.gather(*[_fetch_ticker_data(t) for t in stale])
        for ticker, data in zip(stale, results):
            cache[ticker] = {**data, "cached_at": now.isoformat()}
        _save_enrichment_cache(cache)

    enriched = []
    for p in picks:
        d = cache.get(p.ticker, {})
        price = d.get("current_price")
        mean_t = d.get("mean_target")
        median_t = d.get("median_target")

        mean_upside = ((mean_t - price) / price * 100) if price and mean_t else None
        median_upside = (
            ((median_t - price) / price * 100) if price and median_t else None
        )

        enriched.append(
            p.model_copy(
                update={
                    "current_price": price,
                    "mean_target": mean_t,
                    "median_target": median_t,
                    "mean_upside_pct": mean_upside,
                    "median_upside_pct": median_upside,
                }
            )
        )
    return enriched
