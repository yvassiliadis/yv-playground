from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import api
from src import config as exclusions
from src.models import CommitteeRun


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
