# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "finvizfinance>=0.14.0",
#     "pytickersymbols>=1.17.10",
#     "yahooquery>=2.3.0",
#     "yfinance>=1.4.0",
# ]
# ///

"""
Benchmark four approaches for fetching S&P 500 + NASDAQ 100 fundamentals.

All approaches are timed from first network call to final result.
For per-ticker approaches (yfinance, yahooquery), a 100-ticker sample is used;
server-side screeners (yfinance Screener, finviz) run against their full universe.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field

from pytickersymbols import PyTickerSymbols

SAMPLE_SIZE = 100
MIN_REVENUE_GROWTH = 0.15
MIN_GROSS_MARGIN = 0.40
MIN_ROE = 0.15
MAX_DEBT_TO_EQUITY = 150  # yfinance percentage scale = 1.5x ratio
MIN_MARKET_CAP = 2_000_000_000


@dataclass
class BenchResult:
    name: str
    elapsed: float
    tickers_fetched: int
    tickers_qualified: int
    sample_qualified: list[str] = field(default_factory=list)
    notes: str = ""


def get_universe() -> list[str]:
    p = PyTickerSymbols()
    sp500 = [s["symbol"] for s in p.get_stocks_by_index("S&P 500")]
    nasdaq100 = [s["symbol"] for s in p.get_stocks_by_index("NASDAQ 100")]
    return list(set(sp500) | set(nasdaq100))


def passes_filters(info: dict) -> bool:
    """Lenient: only disqualify when a metric is present AND below threshold."""
    market_cap = info.get("marketCap")
    if market_cap is not None and market_cap < MIN_MARKET_CAP:
        return False
    revenue_growth = info.get("revenueGrowth")
    if revenue_growth is not None and revenue_growth < MIN_REVENUE_GROWTH:
        return False
    gross_margin = info.get("grossMargins")
    if gross_margin is not None and gross_margin < MIN_GROSS_MARGIN:
        return False
    roe = info.get("returnOnEquity")
    if roe is not None and roe < MIN_ROE:
        return False
    debt_to_equity = info.get("debtToEquity")
    if debt_to_equity is not None and debt_to_equity > MAX_DEBT_TO_EQUITY:
        return False
    return True


# ── Approach 1: yfinance per-ticker (current) ──────────────────────────────


async def bench_yfinance(tickers: list[str]) -> BenchResult:
    import yfinance as yf

    sem = asyncio.Semaphore(20)

    async def fetch(ticker: str) -> tuple[str, dict] | None:
        async with sem:
            try:
                info = await asyncio.to_thread(lambda: yf.Ticker(ticker).info)
                return ticker, info
            except Exception:
                return None

    start = time.perf_counter()
    raw = await asyncio.gather(*[fetch(t) for t in tickers])
    elapsed = time.perf_counter() - start

    fetched = [r for r in raw if r is not None]
    qualified = [t for t, info in fetched if passes_filters(info)]

    return BenchResult(
        name="yfinance per-ticker (current)",
        elapsed=elapsed,
        tickers_fetched=len(fetched),
        tickers_qualified=len(qualified),
        sample_qualified=sorted(qualified)[:10],
        notes=f"20 concurrent, {SAMPLE_SIZE}-ticker sample — extrapolate ×{517 // SAMPLE_SIZE} for full universe",
    )


# ── Approach 2: yahooquery bulk ────────────────────────────────────────────


def bench_yahooquery(tickers: list[str]) -> BenchResult:
    from yahooquery import Ticker

    start = time.perf_counter()
    t = Ticker(tickers, asynchronous=False, max_workers=20)
    financial = (
        t.financial_data
    )  # revenueGrowth, grossMargins, returnOnEquity, debtToEquity
    key_stats = t.key_stats  # marketCap lives here

    elapsed = time.perf_counter() - start

    qualified: list[str] = []
    for ticker in tickers:
        fin = financial.get(ticker, {})
        stats = key_stats.get(ticker, {})
        if not isinstance(fin, dict) or not isinstance(stats, dict):
            continue
        mapped = {
            "revenueGrowth": fin.get("revenueGrowth"),
            "grossMargins": fin.get("grossMargins"),
            "returnOnEquity": fin.get("returnOnEquity"),
            "debtToEquity": fin.get("debtToEquity"),
            "marketCap": stats.get("marketCap"),
        }
        if passes_filters(mapped):
            qualified.append(ticker)

    return BenchResult(
        name="yahooquery bulk",
        elapsed=elapsed,
        tickers_fetched=len(tickers),
        tickers_qualified=len(qualified),
        sample_qualified=sorted(qualified)[:10],
        notes=f"batch requests, {SAMPLE_SIZE}-ticker sample",
    )


# ── Approach 3: yfinance Screener (server-side) ────────────────────────────


def bench_yfinance_screener() -> BenchResult:
    from yfinance import EquityQuery, screen

    start = time.perf_counter()
    try:
        # Field names from EquityQuery().valid_fields() — units are decimal ratios
        query = EquityQuery(
            "and",
            [
                EquityQuery("eq", ["region", "us"]),
                EquityQuery("gt", ["totalrevenues1yrgrowth.lasttwelvemonths", MIN_REVENUE_GROWTH]),
                EquityQuery("gt", ["grossprofitmargin.lasttwelvemonths", MIN_GROSS_MARGIN]),
                EquityQuery("gt", ["returnonequity.lasttwelvemonths", MIN_ROE]),
                EquityQuery("lt", ["totaldebtequity.lasttwelvemonths", MAX_DEBT_TO_EQUITY]),
                EquityQuery("gt", ["intradaymarketcap", MIN_MARKET_CAP]),
            ],
        )
        resp = screen(query, size=250)
        quotes = resp.get("quotes", [])
        elapsed = time.perf_counter() - start
        tickers = [q["symbol"] for q in quotes]
        return BenchResult(
            name="yfinance Screener (server-side)",
            elapsed=elapsed,
            tickers_fetched=len(tickers),
            tickers_qualified=len(tickers),
            sample_qualified=sorted(tickers)[:10],
            notes="strict filters (missing metrics disqualify), full universe, single request",
        )
    except Exception as e:
        elapsed = time.perf_counter() - start
        return BenchResult(
            name="yfinance Screener (server-side)",
            elapsed=elapsed,
            tickers_fetched=0,
            tickers_qualified=0,
            notes=f"FAILED: {e}",
        )


# ── Approach 4: finviz server-side ─────────────────────────────────────────


def bench_finviz() -> BenchResult:
    from finvizfinance.screener.overview import Overview

    # finviz uses preset buckets — exact field names and values from filter_dict
    filters = {
        "Sales growthqtr over qtr": "Over 15%",
        "Gross Margin": "Over 40%",
        "Return on Equity": "Over +15%",
        "Debt/Equity": "Under 1",       # stricter than our <1.5x but no closer option
        "Market Cap.": "+Mid (over $2bln)",
    }

    start = time.perf_counter()
    try:
        overview = Overview()
        overview.set_filter(filters_dict=filters)
        df = overview.screener_view(verbose=0)
        elapsed = time.perf_counter() - start

        if df is None or df.empty:
            return BenchResult(
                name="finviz (server-side)",
                elapsed=elapsed,
                tickers_fetched=0,
                tickers_qualified=0,
                notes="empty result — filter names may need adjustment",
            )

        tickers = df["Ticker"].tolist() if "Ticker" in df.columns else []
        return BenchResult(
            name="finviz (server-side)",
            elapsed=elapsed,
            tickers_fetched=len(tickers),
            tickers_qualified=len(tickers),
            sample_qualified=sorted(tickers)[:10],
            notes=(
                "bucket filters: QoQ sales growth >15%, gross margin >40%, ROE >15%, "
                "D/E <1 (stricter than our <1.5x), market cap >$2B"
            ),
        )
    except Exception as e:
        elapsed = time.perf_counter() - start
        return BenchResult(
            name="finviz (server-side)",
            elapsed=elapsed,
            tickers_fetched=0,
            tickers_qualified=0,
            notes=f"FAILED: {e}",
        )


# ── Runner ─────────────────────────────────────────────────────────────────


def print_result(r: BenchResult) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {r.name}")
    print(f"{'─' * 60}")
    print(f"  Time:       {r.elapsed:.2f}s")
    print(f"  Fetched:    {r.tickers_fetched}")
    print(f"  Qualified:  {r.tickers_qualified}")
    if r.sample_qualified:
        print(f"  Sample:     {', '.join(r.sample_qualified)}")
    if r.notes:
        print(f"  Notes:      {r.notes}")


async def main() -> None:
    print("Loading universe...")
    universe = get_universe()
    print(f"Universe: {len(universe)} tickers")

    random.seed(42)
    sample = random.sample(universe, min(SAMPLE_SIZE, len(universe)))
    print(f"Sample: {len(sample)} tickers for per-ticker benchmarks\n")

    results: list[BenchResult] = []

    print("▶ Running yfinance per-ticker (current)...")
    results.append(await bench_yfinance(sample))
    print_result(results[-1])

    print("\n▶ Running yahooquery bulk...")
    results.append(bench_yahooquery(sample))
    print_result(results[-1])

    print("\n▶ Running yfinance Screener (server-side)...")
    results.append(bench_yfinance_screener())
    print_result(results[-1])

    print("\n▶ Running finviz (server-side)...")
    results.append(bench_finviz())
    print_result(results[-1])

    print(f"\n{'═' * 60}")
    print("  SUMMARY")
    print(f"{'═' * 60}")
    for r in results:
        status = "✓" if r.tickers_fetched > 0 else "✗"
        print(f"  {status} {r.elapsed:6.2f}s  {r.name}")


if __name__ == "__main__":
    asyncio.run(main())
