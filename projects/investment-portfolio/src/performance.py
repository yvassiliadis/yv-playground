import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

BENCHMARKS = ["SPY", "VGT", "VTI"]

_PERF_CACHE_PATH = Path(__file__).parent.parent / "data" / "perf_cache.json"
_PERF_CACHE_TTL_SECONDS = 4 * 3600


def _perf_cache_key(tickers: list[str], since: date, end: date) -> str:
    return "|".join(sorted(tickers)) + f"|{since}|{end}"


def _load_perf_cache(tickers: list[str], since: date, end: date) -> pd.DataFrame | None:
    if not _PERF_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_PERF_CACHE_PATH.read_text())
        cached_at = datetime.fromisoformat(data["cached_at"])
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > _PERF_CACHE_TTL_SECONDS:
            return None
        if data.get("key") != _perf_cache_key(tickers, since, end):
            return None
        return pd.read_json(data["df"], orient="split")
    except Exception:
        return None


def _save_perf_cache(tickers: list[str], since: date, end: date, df: pd.DataFrame) -> None:
    try:
        _PERF_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERF_CACHE_PATH.write_text(
            json.dumps(
                {
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "key": _perf_cache_key(tickers, since, end),
                    "df": df.to_json(orient="split"),
                },
                indent=2,
            )
        )
    except Exception:
        pass


def _fetch_returns(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
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

    end = date.today()
    all_tickers = list(set(portfolio_tickers + BENCHMARKS))

    returns = _load_perf_cache(all_tickers, since, end)
    if returns is None:
        returns = _fetch_returns(all_tickers, since, end)
        _save_perf_cache(all_tickers, since, end, returns)

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
