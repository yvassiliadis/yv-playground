import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src import enrichment


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(enrichment, "_ENRICHMENT_CACHE_PATH", tmp_path / "enrichment_cache.json")


async def test_get_live_quote_fetches_and_computes_upside():
    with patch(
        "src.enrichment._fetch_ticker_data",
        return_value={"current_price": 300.0, "mean_target": 330.0, "median_target": 315.0},
    ):
        result = await enrichment.get_live_quote("AAPL")
    assert result["current_price"] == 300.0
    assert result["mean_upside_pct"] == pytest.approx(10.0)
    assert result["median_upside_pct"] == pytest.approx(5.0)


async def test_get_live_quote_handles_missing_targets():
    with patch(
        "src.enrichment._fetch_ticker_data",
        return_value={"current_price": 300.0, "mean_target": None, "median_target": None},
    ):
        result = await enrichment.get_live_quote("AAPL")
    assert result["current_price"] == 300.0
    assert result["mean_upside_pct"] is None
    assert result["median_upside_pct"] is None


async def test_get_live_quote_uses_cache_within_ttl():
    enrichment._ENRICHMENT_CACHE_PATH.write_text(json.dumps({
        "AAPL": {
            "current_price": 300.0,
            "mean_target": 330.0,
            "median_target": 315.0,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
    }))
    with patch("src.enrichment._fetch_ticker_data") as mock_fetch:
        result = await enrichment.get_live_quote("AAPL")
    mock_fetch.assert_not_called()
    assert result["current_price"] == 300.0
