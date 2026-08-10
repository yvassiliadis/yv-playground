from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import api
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


def test_trigger_run_defaults_to_10000():
    client = TestClient(api.app)
    with patch("api._clients", return_value=(AsyncMock(), AsyncMock(), AsyncMock())):
        with patch(
            "api.run_committee",
            AsyncMock(side_effect=lambda ac, oc, gc, investment_amount: _dummy_run(investment_amount)),
        ):
            response = client.post("/api/runs")
    assert response.status_code == 200
    assert response.json()["investment_amount"] == 10000.0


def test_trigger_run_uses_custom_investment_amount():
    client = TestClient(api.app)
    with patch("api._clients", return_value=(AsyncMock(), AsyncMock(), AsyncMock())):
        with patch(
            "api.run_committee",
            AsyncMock(side_effect=lambda ac, oc, gc, investment_amount: _dummy_run(investment_amount)),
        ):
            response = client.post("/api/runs", json={"investment_amount": 10784.0})
    assert response.status_code == 200
    assert response.json()["investment_amount"] == 10784.0
