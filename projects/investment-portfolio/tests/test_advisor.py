import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.advisor import ask_committee
from src.models import PortfolioHolding


def _holding(ticker: str) -> PortfolioHolding:
    return PortfolioHolding(
        ticker=ticker,
        company_name=ticker,
        conviction="core",
        weight=10.0,
        nominated_by=["claude"],
        rationale="Test holding",
    )


def _run(coro):
    return asyncio.run(coro)


def _mock_opinion(recommendation: str, company_name: str = "Test Corp") -> dict:
    return {
        "company_name": company_name,
        "recommendation": recommendation,
        "fits_philosophy": True,
        "take": f"Opinion: {recommendation}",
    }


@pytest.fixture
def clients():
    return AsyncMock(), AsyncMock(), AsyncMock()


def test_both_buy_returns_buy(clients) -> None:
    anthropic_client, openai_client, gemini_client = clients
    with patch(
        "src.advisor.claude_member.get_stock_opinion", return_value=_mock_opinion("buy")
    ):
        with patch(
            "src.advisor.gpt_member.get_stock_opinion",
            return_value=_mock_opinion("buy"),
        ):
            with patch(
                "src.advisor.gemini_member.get_stock_opinion",
                return_value=_mock_opinion("buy"),
            ):
                result = _run(
                    ask_committee(
                        "AAPL", anthropic_client, openai_client, gemini_client, []
                    )
                )
    assert result.recommendation == "buy"


def test_one_buy_one_watch_returns_buy(clients) -> None:
    anthropic_client, openai_client, gemini_client = clients
    with patch(
        "src.advisor.claude_member.get_stock_opinion", return_value=_mock_opinion("buy")
    ):
        with patch(
            "src.advisor.gpt_member.get_stock_opinion",
            return_value=_mock_opinion("watch"),
        ):
            with patch(
                "src.advisor.gemini_member.get_stock_opinion",
                return_value=_mock_opinion("watch"),
            ):
                result = _run(
                    ask_committee(
                        "AAPL", anthropic_client, openai_client, gemini_client, []
                    )
                )
    assert result.recommendation == "buy"


def test_one_watch_one_pass_returns_watch(clients) -> None:
    anthropic_client, openai_client, gemini_client = clients
    with patch(
        "src.advisor.claude_member.get_stock_opinion",
        return_value=_mock_opinion("watch"),
    ):
        with patch(
            "src.advisor.gpt_member.get_stock_opinion",
            return_value=_mock_opinion("pass"),
        ):
            with patch(
                "src.advisor.gemini_member.get_stock_opinion",
                return_value=_mock_opinion("pass"),
            ):
                result = _run(
                    ask_committee(
                        "AAPL", anthropic_client, openai_client, gemini_client, []
                    )
                )
    assert result.recommendation == "watch"


def test_both_pass_returns_pass(clients) -> None:
    anthropic_client, openai_client, gemini_client = clients
    with patch(
        "src.advisor.claude_member.get_stock_opinion",
        return_value=_mock_opinion("pass"),
    ):
        with patch(
            "src.advisor.gpt_member.get_stock_opinion",
            return_value=_mock_opinion("pass"),
        ):
            with patch(
                "src.advisor.gemini_member.get_stock_opinion",
                return_value=_mock_opinion("pass"),
            ):
                result = _run(
                    ask_committee(
                        "AAPL", anthropic_client, openai_client, gemini_client, []
                    )
                )
    assert result.recommendation == "pass"


def test_ticker_in_portfolio_returns_already_in(clients) -> None:
    anthropic_client, openai_client, gemini_client = clients
    with patch(
        "src.advisor.claude_member.get_stock_opinion", return_value=_mock_opinion("buy")
    ):
        with patch(
            "src.advisor.gpt_member.get_stock_opinion",
            return_value=_mock_opinion("buy"),
        ):
            with patch(
                "src.advisor.gemini_member.get_stock_opinion",
                return_value=_mock_opinion("buy"),
            ):
                result = _run(
                    ask_committee(
                        "AAPL",
                        anthropic_client,
                        openai_client,
                        gemini_client,
                        [_holding("AAPL"), _holding("MSFT")],
                    )
                )
    assert result.recommendation == "already in portfolio"


def test_ticker_matching_is_case_insensitive(clients) -> None:
    anthropic_client, openai_client, gemini_client = clients
    with patch(
        "src.advisor.claude_member.get_stock_opinion", return_value=_mock_opinion("buy")
    ):
        with patch(
            "src.advisor.gpt_member.get_stock_opinion",
            return_value=_mock_opinion("buy"),
        ):
            with patch(
                "src.advisor.gemini_member.get_stock_opinion",
                return_value=_mock_opinion("buy"),
            ):
                result = _run(
                    ask_committee(
                        "aapl",
                        anthropic_client,
                        openai_client,
                        gemini_client,
                        [_holding("AAPL")],
                    )
                )
    assert result.recommendation == "already in portfolio"


def test_cached_opinion_from_other_model_is_refreshed() -> None:
    from src import advisor

    cached = {
        "claude": _mock_opinion("buy"),
        "gpt": _mock_opinion("buy"),
        "gemini": _mock_opinion("buy"),
        "models": {
            "claude": "some-older-model",
            "gpt": advisor._ADVISOR_MODELS["gpt"],
            "gemini": advisor._ADVISOR_MODELS["gemini"],
        },
        "upside": {},
        "yf_company_name": "Test Corp",
    }
    claude_opinion = AsyncMock(return_value=_mock_opinion("watch"))
    gpt_opinion = AsyncMock()
    gemini_opinion = AsyncMock()
    saved = {}

    with patch("src.advisor._resolve_ticker", return_value="AAPL"), \
         patch("src.advisor._get_cached", return_value=cached), \
         patch("src.advisor._set_cached", side_effect=lambda t, d: saved.update(d)), \
         patch("src.advisor._fetch_ticker_info", return_value=("", {}, "Test Corp")), \
         patch("src.advisor._format_portfolio_context", return_value=""), \
         patch("src.advisor.claude_member.get_stock_opinion", claude_opinion), \
         patch("src.advisor.gpt_member.get_stock_opinion", gpt_opinion), \
         patch("src.advisor.gemini_member.get_stock_opinion", gemini_opinion):
        _run(ask_committee("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), []))

    claude_opinion.assert_called_once()
    gpt_opinion.assert_not_called()
    gemini_opinion.assert_not_called()
    assert saved["models"]["claude"] == advisor._ADVISOR_MODELS["claude"]
