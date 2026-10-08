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


import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src import runner
from src.models import Pick


def _valid_picks(member: str) -> list[Pick]:
    core = [
        Pick(
            ticker=f"CORE{i}",
            company_name=f"Core {i}",
            rationale="test",
            conviction="core",
            member=member,
        )
        for i in range(10)
    ]
    moonshots = [
        Pick(
            ticker=f"MOON{i}",
            company_name=f"Moon {i}",
            rationale="test",
            conviction="moonshot",
            member=member,
        )
        for i in range(3)
    ]
    return core + moonshots


@pytest.fixture(autouse=True)
def _isolate_runner_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "_PICKS_CACHE_DIR", tmp_path / "picks_cache")


async def _run_committee_with_mocks(**kwargs):
    with patch("src.runner.screen_universe", return_value=[]), \
         patch("src.runner.format_for_prompt", return_value=""), \
         patch("src.runner.claude_member.get_research", return_value=("research", [])), \
         patch("src.runner.claude_member.get_picks", return_value=_valid_picks("claude")), \
         patch("src.runner.gpt_member.get_picks", return_value=_valid_picks("gpt")), \
         patch("src.runner.gemini_member.get_picks", return_value=_valid_picks("gemini")), \
         patch("src.runner.enrich_picks_with_prices", side_effect=lambda picks: picks):
        return await runner.run_committee(AsyncMock(), AsyncMock(), AsyncMock(), **kwargs)


def test_run_committee_defaults_investment_amount_to_10000():
    run = asyncio.run(_run_committee_with_mocks())
    assert run.investment_amount == 10000.0


def test_run_committee_stores_custom_investment_amount():
    run = asyncio.run(_run_committee_with_mocks(investment_amount=10784.0))
    assert run.investment_amount == 10784.0


async def _run_committee_with_gpt_picks_mock(gpt_get_picks):
    with patch("src.runner.screen_universe", return_value=[]), \
         patch("src.runner.format_for_prompt", return_value=""), \
         patch("src.runner.claude_member.get_research", return_value=("research", [])), \
         patch("src.runner.claude_member.get_picks", return_value=_valid_picks("claude")), \
         patch("src.runner.gpt_member.get_picks", gpt_get_picks), \
         patch("src.runner.gemini_member.get_picks", return_value=_valid_picks("gemini")), \
         patch("src.runner.enrich_picks_with_prices", side_effect=lambda picks: picks):
        return await runner.run_committee(AsyncMock(), AsyncMock(), AsyncMock())


def test_run_committee_reuses_picks_cached_for_same_model():
    runner._save_picks_cache(
        "gpt", runner.gpt_member.PICKS_MODEL, _valid_picks("gpt"), []
    )
    gpt_get_picks = AsyncMock(return_value=_valid_picks("gpt"))
    asyncio.run(_run_committee_with_gpt_picks_mock(gpt_get_picks))
    gpt_get_picks.assert_not_called()


def test_run_committee_ignores_picks_cached_for_other_model():
    runner._save_picks_cache("gpt", "some-older-model", _valid_picks("gpt"), [])
    gpt_get_picks = AsyncMock(return_value=_valid_picks("gpt"))
    asyncio.run(_run_committee_with_gpt_picks_mock(gpt_get_picks))
    gpt_get_picks.assert_called_once()


def test_run_committee_does_not_cache_picks_with_wrong_moonshot_count():
    bad_picks = [p for p in _valid_picks("gpt") if p.conviction == "core"]
    gpt_get_picks = AsyncMock(return_value=bad_picks)
    run = asyncio.run(_run_committee_with_gpt_picks_mock(gpt_get_picks))
    assert run.gpt_picks == []
    assert runner._load_picks_cache("gpt", runner.gpt_member.PICKS_MODEL) is None


def test_research_cache_ignored_for_other_model():
    runner._save_research_cache("some-older-model", "briefing", [])
    assert runner._load_research_cache("some-older-model") == ("briefing", [])
    assert runner._load_research_cache("another-model") is None
