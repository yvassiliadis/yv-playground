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
