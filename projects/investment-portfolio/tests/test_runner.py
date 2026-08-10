from src.models import CommitteeRun


def test_committee_run_defaults_investment_amount_to_10000():
    run = CommitteeRun(
        run_id="abc",
        timestamp="2026-01-01T00:00:00Z",
        claude_picks=[],
        gpt_picks=[],
        portfolio=[],
    )
    assert run.investment_amount == 10000.0


def test_committee_run_investment_amount_round_trips():
    run = CommitteeRun(
        run_id="abc",
        timestamp="2026-01-01T00:00:00Z",
        claude_picks=[],
        gpt_picks=[],
        portfolio=[],
        investment_amount=10784.0,
    )
    data = run.model_dump()
    restored = CommitteeRun.model_validate(data)
    assert restored.investment_amount == 10784.0


def test_committee_run_loads_old_data_without_investment_amount():
    old_data = {
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "claude_picks": [],
        "gpt_picks": [],
        "portfolio": [],
    }
    run = CommitteeRun.model_validate(old_data)
    assert run.investment_amount == 10000.0
