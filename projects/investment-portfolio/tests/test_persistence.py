import json
from datetime import datetime, timezone
from src.models import CommitteeRun, Pick, PortfolioHolding


def _make_run() -> CommitteeRun:
    pick = Pick(
        ticker="AAPL",
        company_name="Apple Inc.",
        rationale="Strong moat",
        conviction="core",
        member="claude",
    )
    holding = PortfolioHolding(
        ticker="AAPL",
        company_name="Apple Inc.",
        conviction="core",
        weight=100.0,
        nominated_by=["claude"],
        rationale="Strong moat",
    )
    return CommitteeRun(
        run_id="test-run-1",
        timestamp=datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        claude_picks=[pick],
        gpt_picks=[],
        portfolio=[holding],
    )


def test_round_trip_json() -> None:
    run = _make_run()
    serialized = run.model_dump_json()
    restored = CommitteeRun.model_validate(json.loads(serialized))

    assert restored.run_id == run.run_id
    assert restored.timestamp == run.timestamp
    assert len(restored.claude_picks) == 1
    assert restored.claude_picks[0].ticker == "AAPL"
    assert len(restored.portfolio) == 1
    assert restored.portfolio[0].weight == 100.0


def test_timestamp_preserved_with_timezone() -> None:
    run = _make_run()
    serialized = run.model_dump_json()
    restored = CommitteeRun.model_validate(json.loads(serialized))
    assert restored.timestamp.tzinfo is not None
