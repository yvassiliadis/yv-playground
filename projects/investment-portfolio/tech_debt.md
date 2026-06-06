# Tech Debt

## Tracker cache duplicates perf cache infrastructure

`src/performance.py` contains two near-identical sets of cache functions:
- `_load_perf_cache` / `_save_perf_cache` (used by `portfolio_vs_benchmarks`)
- `_load_tracker_cache` / `_save_tracker_cache` (used by `tracked_portfolios_performance`)

The only real differences are the cache file path and the key-building logic. A shared
`_load_cache(path, key)` / `_save_cache(path, key, payload)` pair would eliminate the
duplication and ensure TTL behavior stays consistent across both callers.

## Double yfinance fetch per tracker page load

When the tracker view loads, the browser fires two independent requests:
- `GET /api/portfolios` → `get_enriched_portfolios()` → `get_current_prices()` (per-ticker `.info` calls via yfinance)
- `GET /api/portfolios/performance` → `tracked_portfolios_performance()` → `yf.download(all_tickers, ...)` (full year of OHLC history)

Both hit yfinance for the same ticker set. The performance download already has the close
prices needed to derive spot values, so a combined endpoint (or extracting spot prices from
the last row of the OHLC download) could eliminate the first call entirely.
