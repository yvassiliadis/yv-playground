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


def _save_perf_cache(
    tickers: list[str], since: date, end: date, df: pd.DataFrame
) -> None:
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
    end = date.today()

    all_tickers: set[str] = {"SPY", "VTI"}
    for p in portfolios:
        for pos in p.positions:
            all_tickers.add(pos.ticker)
    if committee:
        all_tickers.update(committee["tickers"])

    raw = yf.download(
        list(all_tickers), start=since, end=end, auto_adjust=True, progress=False
    )
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
        if ticker in closes.columns:
            result[ticker.lower()] = {"type": "benchmark", **summary(closes[ticker])}

    if committee:
        ct = committee["tickers"]
        cw = {t: w / 100 for t, w in zip(ct, committee["weights"])}
        available_ct = [t for t in ct if t in closes.columns]
        if available_ct:
            comm_value = sum(closes[t] * cw[t] for t in available_ct)
            result["committee"] = {"type": "committee", **summary(comm_value)}

    return result
