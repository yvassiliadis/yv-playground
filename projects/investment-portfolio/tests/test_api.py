from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import api
from src import config as exclusions
from src import portfolios, runner
from src.models import CommitteeRun, PortfolioHolding, PortfolioPosition, TrackedPortfolio


def _dummy_run(investment_amount: float) -> CommitteeRun:
    return CommitteeRun(
        run_id="abc",
        timestamp="2026-01-01T00:00:00Z",
        claude_picks=[],
        gpt_picks=[],
        portfolio=[],
        investment_amount=investment_amount,
    )


def test_trigger_run_defaults_to_10000(monkeypatch):
    monkeypatch.setattr(exclusions, "INVESTMENT_AMOUNT", 10000.0)
    client = TestClient(api.app)
    with patch("api._clients", return_value=(AsyncMock(), AsyncMock(), AsyncMock())):
        with patch(
            "api.run_committee",
            AsyncMock(side_effect=lambda ac, oc, gc, investment_amount: _dummy_run(investment_amount)),
        ):
            response = client.post("/api/runs")
    assert response.status_code == 200
    assert response.json()["investment_amount"] == 10000.0


def test_trigger_run_uses_custom_investment_amount(monkeypatch):
    monkeypatch.setattr(exclusions, "INVESTMENT_AMOUNT", 10784.0)
    client = TestClient(api.app)
    with patch("api._clients", return_value=(AsyncMock(), AsyncMock(), AsyncMock())):
        with patch(
            "api.run_committee",
            AsyncMock(side_effect=lambda ac, oc, gc, investment_amount: _dummy_run(investment_amount)),
        ):
            response = client.post("/api/runs")
    assert response.status_code == 200
    assert response.json()["investment_amount"] == 10784.0


def test_get_quote_returns_live_values():
    client = TestClient(api.app)
    with patch(
        "api.get_live_quote",
        AsyncMock(return_value={"current_price": 300.0, "mean_upside_pct": 10.0, "median_upside_pct": 5.0}),
    ):
        response = client.get("/api/quote/AAPL")
    assert response.status_code == 200
    assert response.json() == {"current_price": 300.0, "mean_upside_pct": 10.0, "median_upside_pct": 5.0}


def test_get_quote_returns_nulls_on_failure():
    client = TestClient(api.app)
    with patch("api.get_live_quote", AsyncMock(side_effect=RuntimeError("yfinance boom"))):
        response = client.get("/api/quote/AAPL")
    assert response.status_code == 200
    assert response.json() == {"current_price": None, "mean_upside_pct": None, "median_upside_pct": None}


@pytest.fixture
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(exclusions, "_EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    monkeypatch.setattr(exclusions, "INVESTMENT_AMOUNT", 10000.0)
    monkeypatch.setattr(exclusions, "TAX_RATE", 0.24)
    monkeypatch.setattr(exclusions, "MIN_TRADE", 25.0)
    monkeypatch.setattr(exclusions, "REBALANCE_PORTFOLIO", None)


def test_settings_round_trip(_isolated_settings):
    client = TestClient(api.app)
    payload = {
        "investment_amount": 15000.0,
        "tax_rate": 0.15,
        "min_trade": 50.0,
        "rebalance_portfolio": "core-growth",
    }
    put_response = client.put("/api/settings", json=payload)
    assert put_response.status_code == 200

    get_response = client.get("/api/settings")
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["investment_amount"] == 15000.0
    assert body["tax_rate"] == 0.15
    assert body["min_trade"] == 50.0
    assert body["rebalance_portfolio"] == "core-growth"


def test_settings_rejects_non_positive_investment_amount(_isolated_settings):
    client = TestClient(api.app)
    response = client.put("/api/settings", json={"investment_amount": 0})
    assert response.status_code == 400
    assert exclusions.INVESTMENT_AMOUNT == 10000.0


def test_settings_rejects_tax_rate_out_of_range(_isolated_settings):
    client = TestClient(api.app)
    response = client.put("/api/settings", json={"tax_rate": 1.0})
    assert response.status_code == 400
    assert exclusions.TAX_RATE == 0.24


def test_settings_rejects_negative_min_trade(_isolated_settings):
    client = TestClient(api.app)
    response = client.put("/api/settings", json={"min_trade": -1.0})
    assert response.status_code == 400
    assert exclusions.MIN_TRADE == 25.0


def test_settings_rejects_malformed_type_with_422_not_500(_isolated_settings):
    client = TestClient(api.app)
    response = client.put("/api/settings", json={"investment_amount": "not a number"})
    assert response.status_code == 422
    assert exclusions.INVESTMENT_AMOUNT == 10000.0


def test_settings_rejects_bool_investment_amount(_isolated_settings):
    # Pydantic v2's default lax mode treats bool as an int subtype and would
    # otherwise silently coerce True/False into 1.0/0.0; the field is strict
    # to close that gap.
    client = TestClient(api.app)
    response = client.put("/api/settings", json={"investment_amount": True})
    assert response.status_code == 422
    assert exclusions.INVESTMENT_AMOUNT == 10000.0


def test_settings_clearing_rebalance_portfolio_with_explicit_null(_isolated_settings):
    client = TestClient(api.app)
    client.put("/api/settings", json={"rebalance_portfolio": "core-growth"})
    response = client.put("/api/settings", json={"rebalance_portfolio": None})
    assert response.status_code == 200
    assert exclusions.REBALANCE_PORTFOLIO is None


@pytest.fixture
def _isolated_rebalance(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolios, "_PORTFOLIOS_PATH", tmp_path / "portfolios.json")
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(exclusions, "INVESTMENT_AMOUNT", 1000.0)
    monkeypatch.setattr(exclusions, "TAX_RATE", 0.24)
    monkeypatch.setattr(exclusions, "MIN_TRADE", 25.0)
    return tmp_path


def _seed_run(tmp_path, holdings):
    run = CommitteeRun(
        run_id="abc",
        timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        claude_picks=[],
        gpt_picks=[],
        portfolio=holdings,
    )
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_file = runs_dir / f"{run.timestamp.strftime('%Y%m%d_%H%M%S')}.json"
    run_file.write_text(run.model_dump_json(indent=2))


_TARGET_HOLDINGS = [
    PortfolioHolding(
        ticker="AAPL",
        company_name="Apple",
        conviction="core",
        weight=50.0,
        nominated_by=["claude", "gpt"],
        rationale="steady",
    ),
    PortfolioHolding(
        ticker="MSFT",
        company_name="Microsoft",
        conviction="core",
        weight=50.0,
        nominated_by=["claude", "gpt"],
        rationale="steady",
    ),
]


def test_rebalance_happy_path(_isolated_rebalance):
    tmp_path = _isolated_rebalance
    portfolios.save(
        [
            TrackedPortfolio(
                name="Retirement",
                positions=[PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=100.0)],
            )
        ]
    )
    _seed_run(tmp_path, _TARGET_HOLDINGS)

    client = TestClient(api.app)
    with patch(
        "api.get_current_prices",
        AsyncMock(return_value={"AAPL": 200.0, "MSFT": 300.0}),
    ):
        response = client.get("/api/rebalance", params={"portfolio": "Retirement"})

    assert response.status_code == 200
    body = response.json()
    for key in (
        "trades",
        "current_total",
        "target_total",
        "cash_withdrawn",
        "total_buys",
        "total_sells",
        "est_tax",
        "full_liquidation_tax",
        "warnings",
    ):
        assert key in body
    tickers = {t["ticker"] for t in body["trades"]}
    assert tickers == {"AAPL", "MSFT"}


def test_rebalance_404_when_no_run_exists(_isolated_rebalance):
    client = TestClient(api.app)
    response = client.get("/api/rebalance", params={"portfolio": "Retirement"})
    assert response.status_code == 404


def test_rebalance_404_when_portfolio_not_found(_isolated_rebalance):
    tmp_path = _isolated_rebalance
    _seed_run(tmp_path, _TARGET_HOLDINGS)

    client = TestClient(api.app)
    response = client.get("/api/rebalance", params={"portfolio": "Nonexistent"})
    assert response.status_code == 404


def test_rebalance_400_when_amount_exceeds_current_value(_isolated_rebalance, monkeypatch):
    tmp_path = _isolated_rebalance
    portfolios.save(
        [
            TrackedPortfolio(
                name="Retirement",
                positions=[PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=100.0)],
            )
        ]
    )
    _seed_run(tmp_path, _TARGET_HOLDINGS)
    monkeypatch.setattr(exclusions, "INVESTMENT_AMOUNT", 5000.0)

    client = TestClient(api.app)
    with patch(
        "api.get_current_prices",
        AsyncMock(return_value={"AAPL": 200.0, "MSFT": 300.0}),
    ):
        response = client.get("/api/rebalance", params={"portfolio": "Retirement"})

    assert response.status_code == 400


def test_rebalance_400_when_amount_exceeds_priced_total_despite_unpriced_position(
    _isolated_rebalance, monkeypatch
):
    # Regression test: portfolios.enrich()'s total_value is None as soon as ANY
    # position lacks a price, which used to make the 400 guard never fire for a
    # portfolio with a mix of priced and unpriced positions. The fixed check
    # uses the rebalance engine's own current_total (priced rows only), so it
    # must still 400 here even though ZETA has no price.
    tmp_path = _isolated_rebalance
    portfolios.save(
        [
            TrackedPortfolio(
                name="Retirement",
                positions=[
                    PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=100.0),
                    PortfolioPosition(ticker="ZETA", shares=5.0, avg_cost=10.0),
                ],
            )
        ]
    )
    _seed_run(tmp_path, _TARGET_HOLDINGS)
    # AAPL alone is worth 10 * 200 = 2000; ZETA has no price. Investment amount
    # exceeds the priced total (2000) even though the whole-portfolio total is
    # unknown (None) because of the unpriced ZETA position.
    monkeypatch.setattr(exclusions, "INVESTMENT_AMOUNT", 3000.0)

    client = TestClient(api.app)
    with patch(
        "api.get_current_prices",
        AsyncMock(return_value={"AAPL": 200.0, "MSFT": 300.0, "ZETA": None}),
    ):
        response = client.get("/api/rebalance", params={"portfolio": "Retirement"})

    assert response.status_code == 400
    assert "3,000.00" in response.json()["detail"]
    assert "2,000.00" in response.json()["detail"]
