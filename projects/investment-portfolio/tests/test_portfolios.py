import pytest

from src import portfolios
from src.models import PortfolioPosition, TrackedPortfolio


def test_position_without_avg_cost():
    pos = PortfolioPosition(ticker="AAPL", shares=10.0)
    assert pos.avg_cost is None
    assert pos.ticker == "AAPL"


def test_portfolio_round_trips_json():
    portfolio = TrackedPortfolio(
        name="My Portfolio",
        positions=[
            PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=150.0),
            PortfolioPosition(ticker="VTI", shares=50.0),
        ],
    )
    data = portfolio.model_dump()
    restored = TrackedPortfolio.model_validate(data)
    assert restored.name == "My Portfolio"
    assert len(restored.positions) == 2
    assert restored.positions[0].avg_cost == 150.0
    assert restored.positions[1].avg_cost is None


def test_load_returns_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolios, "_PORTFOLIOS_PATH", tmp_path / "portfolios.json")
    assert portfolios.load() == []


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolios, "_PORTFOLIOS_PATH", tmp_path / "portfolios.json")
    data = [
        TrackedPortfolio(
            name="Retirement",
            positions=[PortfolioPosition(ticker="VTI", shares=100.0, avg_cost=220.50)],
        )
    ]
    portfolios.save(data)
    loaded = portfolios.load()
    assert len(loaded) == 1
    assert loaded[0].name == "Retirement"
    assert loaded[0].positions[0].shares == 100.0
    assert loaded[0].positions[0].avg_cost == 220.50


def test_parse_csv_basic():
    csv_content = "ticker,shares,avg_cost\nAAPL,10,150.00\nVTI,50,\n"
    positions = portfolios.parse_csv(csv_content)
    assert len(positions) == 2
    assert positions[0].ticker == "AAPL"
    assert positions[0].shares == 10.0
    assert positions[0].avg_cost == 150.0
    assert positions[1].ticker == "VTI"
    assert positions[1].avg_cost is None


def test_parse_csv_skips_invalid_rows():
    csv_content = "ticker,shares,avg_cost\nAAPL,10,\n,15,100\nMSFT,notanumber,\n"
    positions = portfolios.parse_csv(csv_content)
    assert len(positions) == 1
    assert positions[0].ticker == "AAPL"


def test_parse_csv_handles_excel_bom():
    # Excel-exported CSVs often start with a UTF-8 BOM
    csv_content = "﻿ticker,shares,avg_cost\nAAPL,10,\n"
    positions = portfolios.parse_csv(csv_content)
    assert len(positions) == 1
    assert positions[0].ticker == "AAPL"


async def test_enrich_computes_value_and_return():
    portfolio = TrackedPortfolio(
        name="Test",
        positions=[
            PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=150.0),
            PortfolioPosition(ticker="VTI", shares=100.0),
        ],
    )
    prices = {"AAPL": 200.0, "VTI": 250.0}
    result = await portfolios.enrich(portfolio, prices)
    aapl = next(p for p in result["positions"] if p["ticker"] == "AAPL")
    assert aapl["total_value"] == 2000.0
    assert aapl["return_pct"] == pytest.approx(33.3, abs=0.1)
    assert result["total_value"] == 27000.0
    assert result["total_return_pct"] is None  # VTI has no avg_cost


async def test_enrich_total_return_when_all_have_cost():
    portfolio = TrackedPortfolio(
        name="Test",
        positions=[PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=150.0)],
    )
    prices = {"AAPL": 180.0}
    result = await portfolios.enrich(portfolio, prices)
    assert result["total_return_pct"] == pytest.approx(20.0)


async def test_enrich_handles_missing_price():
    portfolio = TrackedPortfolio(
        name="Test",
        positions=[PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=150.0)],
    )
    prices = {"AAPL": None}
    result = await portfolios.enrich(portfolio, prices)
    assert result["total_value"] is None
    assert result["positions"][0]["total_value"] is None
