from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from src.models import PortfolioPosition, TrackedPortfolio
from src.performance import tracked_portfolios_performance


def _fake_closes(tickers, n=10):
    """All tickers start at 100, grow 1%/day."""
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {t: [100 * (1.01**i) for i in range(n)] for t in tickers}, index=idx
    )


def test_portfolio_series_per_portfolio():
    portfolios = [
        TrackedPortfolio(
            name="P1", positions=[PortfolioPosition(ticker="AAPL", shares=10.0)]
        ),
        TrackedPortfolio(
            name="P2", positions=[PortfolioPosition(ticker="MSFT", shares=5.0)]
        ),
    ]
    closes = _fake_closes(["AAPL", "MSFT", "SPY", "VTI"])
    with patch("src.performance.yf.download", return_value=closes):
        result = tracked_portfolios_performance(portfolios)
    assert "P1" in result
    assert "P2" in result
    assert result["P1"]["type"] == "portfolio"


def test_benchmarks_always_included():
    portfolios = [
        TrackedPortfolio(
            name="P1", positions=[PortfolioPosition(ticker="AAPL", shares=10.0)]
        )
    ]
    closes = _fake_closes(["AAPL", "SPY", "VTI"])
    with patch("src.performance.yf.download", return_value=closes):
        result = tracked_portfolios_performance(portfolios)
    assert "spy" in result
    assert "vti" in result
    assert result["spy"]["type"] == "benchmark"


def test_committee_included_when_provided():
    portfolios = [
        TrackedPortfolio(
            name="P1", positions=[PortfolioPosition(ticker="AAPL", shares=10.0)]
        )
    ]
    closes = _fake_closes(["AAPL", "SPY", "VTI", "MSFT"])
    with patch("src.performance.yf.download", return_value=closes):
        result = tracked_portfolios_performance(
            portfolios,
            committee={"tickers": ["AAPL", "MSFT"], "weights": [60.0, 40.0]},
        )
    assert "committee" in result
    assert result["committee"]["type"] == "committee"


def test_series_normalized_to_one_at_start():
    portfolios = [
        TrackedPortfolio(
            name="P1", positions=[PortfolioPosition(ticker="AAPL", shares=10.0)]
        )
    ]
    closes = _fake_closes(["AAPL", "SPY", "VTI"])
    with patch("src.performance.yf.download", return_value=closes):
        result = tracked_portfolios_performance(portfolios)
    first_val = list(result["P1"]["series"].values())[0]
    assert abs(first_val - 1.0) < 1e-6


def test_empty_portfolios_returns_benchmarks_only():
    closes = _fake_closes(["SPY", "VTI"])
    with patch("src.performance.yf.download", return_value=closes):
        result = tracked_portfolios_performance([])
    assert "spy" in result
    assert "vti" in result
    assert all(v["type"] == "benchmark" for v in result.values())
