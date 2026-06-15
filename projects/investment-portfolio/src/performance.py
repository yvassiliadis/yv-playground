import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

_DOWNLOAD_MAX_RETRIES = 3
_DOWNLOAD_INITIAL_BACKOFF_SECONDS = 1.0

BENCHMARKS = ["SPY", "VGT", "VTI"]

_PERF_CACHE_PATH = Path(__file__).parent.parent / "data" / "perf_cache.json"
_TRACKER_PERF_CACHE_PATH = (
    Path(__file__).parent.parent / "data" / "tracker_perf_cache.json"
)
_PERF_CACHE_TTL_SECONDS = 4 * 3600


def _perf_cache_key(tickers: list[str], since: date) -> str:
    return "|".join(sorted(tickers)) + f"|{since}"


def _load_perf_cache(tickers: list[str], since: date) -> pd.DataFrame | None:
    if not _PERF_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_PERF_CACHE_PATH.read_text())
        cached_at = datetime.fromisoformat(data["cached_at"])
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > _PERF_CACHE_TTL_SECONDS:
            return None
        if data.get("key") != _perf_cache_key(tickers, since):
            return None
        return pd.read_json(data["df"], orient="split")
    except Exception:
        return None


def _save_perf_cache(tickers: list[str], since: date, df: pd.DataFrame) -> None:
    try:
        _PERF_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERF_CACHE_PATH.write_text(
            json.dumps(
                {
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "key": _perf_cache_key(tickers, since),
                    "df": df.to_json(orient="split"),
                },
                indent=2,
            )
        )
    except Exception:
        pass


def _download_prices(tickers: list[str], start: date) -> pd.DataFrame:
    """Downloads price data with retry on empty result (Yahoo Finance rate limits)."""
    backoff = _DOWNLOAD_INITIAL_BACKOFF_SECONDS
    for attempt in range(_DOWNLOAD_MAX_RETRIES):
        raw = yf.download(tickers, start=start, auto_adjust=True, progress=False)
        if not raw.empty:
            return raw
        if attempt < _DOWNLOAD_MAX_RETRIES - 1:
            log.warning("yfinance returned empty data, retrying in %.0fs", backoff)
            time.sleep(backoff)
            backoff *= 2
    return raw


def _fetch_returns(tickers: list[str], start: date) -> pd.DataFrame:
    raw = _download_prices(tickers, start=start)
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    if closes.empty:
        raise ValueError(
            f"No price data available for {len(tickers)} tickers from {start}"
        )
    return closes / closes.iloc[0]


def portfolio_vs_benchmarks(
    portfolio_tickers: list[str],
    portfolio_weights: list[float],  # must sum to 100
    since: Optional[date] = None,
) -> dict:
    """
    Compares portfolio performance against SPY, VGT, and VTI.
    Returns a dict with cumulative return series and summary stats.
    """
    if since is None:
        since = date.today() - timedelta(days=365)

    all_tickers = list(set(portfolio_tickers + BENCHMARKS))

    returns = _load_perf_cache(all_tickers, since)
    if returns is None:
        try:
            returns = _fetch_returns(all_tickers, since)
        except ValueError:
            log.warning("Price fetch failed for performance chart, returning empty")
            return {}
        _save_perf_cache(all_tickers, since, returns)

    weights = {t: w / 100 for t, w in zip(portfolio_tickers, portfolio_weights)}
    available = [t for t in portfolio_tickers if t in returns.columns]
    if not available:
        return {}

    portfolio_series = sum(returns[t] * weights[t] for t in available)

    def summary(series: pd.Series) -> dict:
        total_return = float((series.iloc[-1] - 1) * 100)
        return {"total_return_pct": round(total_return, 2), "series": series.to_dict()}

    result = {"portfolio": summary(portfolio_series)}
    for ticker in BENCHMARKS:
        if ticker in returns.columns:
            result[ticker.lower()] = summary(returns[ticker])

    return result


def _tracker_cache_key(portfolios: list, committee: Optional[dict], since: date) -> str:
    parts = []
    for p in sorted(portfolios, key=lambda x: x.name):
        pos_str = ",".join(
            f"{pos.ticker}:{pos.shares}"
            for pos in sorted(p.positions, key=lambda x: x.ticker)
        )
        parts.append(f"{p.name}:{pos_str}")
    if committee:
        parts.append("committee:" + ",".join(sorted(committee["tickers"])))
    parts.append(str(since))
    return "|".join(parts)


def _load_tracker_cache(
    portfolios: list, committee: Optional[dict], since: date
) -> dict | None:
    if not _TRACKER_PERF_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_TRACKER_PERF_CACHE_PATH.read_text())
        cached_at = datetime.fromisoformat(data["cached_at"])
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > _PERF_CACHE_TTL_SECONDS:
            return None
        if data.get("key") != _tracker_cache_key(portfolios, committee, since):
            return None
        return data["result"]
    except Exception:
        return None


def _save_tracker_cache(
    portfolios: list, committee: Optional[dict], since: date, result: dict
) -> None:
    try:
        _TRACKER_PERF_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TRACKER_PERF_CACHE_PATH.write_text(
            json.dumps(
                {
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "key": _tracker_cache_key(portfolios, committee, since),
                    "result": result,
                },
                indent=2,
            )
        )
    except Exception:
        pass


def tracked_portfolios_performance(
    portfolios: list,  # list[TrackedPortfolio]
    since: Optional[date] = None,
    committee: Optional[dict] = None,  # {"tickers": [...], "weights": [...]}
) -> dict:
    """
    Compares tracked portfolios (defined by share counts) against SPY, VTI,
    and optionally a weight-based committee portfolio.
    Returns a dict keyed by portfolio name / benchmark ticker.
    """
    if since is None:
        since = date.today() - timedelta(days=365)

    cached = _load_tracker_cache(portfolios, committee, since)
    if cached is not None:
        return cached

    all_tickers: set[str] = {"SPY", "VTI"}
    for p in portfolios:
        for pos in p.positions:
            all_tickers.add(pos.ticker)
    if committee:
        all_tickers.update(committee["tickers"])

    raw = _download_prices(list(all_tickers), start=since)
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw

    def summary(series: pd.Series) -> dict:
        normalized = series / series.iloc[0]
        total_return = float((normalized.iloc[-1] - 1) * 100)
        return {
            "total_return_pct": round(total_return, 2),
            "series": normalized.to_dict(),
        }

    result: dict = {}

    for p in portfolios:
        available = [pos for pos in p.positions if pos.ticker in closes.columns]
        if not available:
            continue
        shares = {pos.ticker: pos.shares for pos in available}
        daily_value = pd.Series(0.0, index=closes.index)
        for t, s in shares.items():
            daily_value += s * closes[t].ffill()
        daily_value = daily_value[daily_value > 0]
        if daily_value.empty:
            continue
        result[p.name] = {"type": "portfolio", **summary(daily_value)}

    for ticker in ["SPY", "VTI"]:
        col = closes.get(ticker)
        if col is not None and col.notna().any():
            result[ticker.lower()] = {"type": "benchmark", **summary(col)}

    if committee:
        ct = committee["tickers"]
        cw = {t: w / 100 for t, w in zip(ct, committee["weights"])}
        available_ct = [
            t for t in ct if t in closes.columns and closes[t].notna().any()
        ]
        if available_ct:
            comm_value = sum(closes[t] * cw[t] for t in available_ct)
            result["committee"] = {"type": "committee", **summary(comm_value)}

    if result:
        _save_tracker_cache(portfolios, committee, since, result)
    return result
