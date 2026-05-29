import pytest
from src.committee.aggregator import build_portfolio
from src.models import Pick


def _pick(ticker: str, conviction: str, member: str) -> Pick:
    return Pick(
        ticker=ticker,
        company_name=f"{ticker} Inc.",
        rationale="test",
        conviction=conviction,
        member=member,
    )


def test_weights_sum_to_100() -> None:
    picks = [
        _pick("AAPL", "core", "claude"),
        _pick("MSFT", "core", "gpt"),
        _pick("NVDA", "moonshot", "claude"),
    ]
    portfolio = build_portfolio(picks)
    total = sum(h.weight for h in portfolio)
    assert abs(total - 100.0) < 0.01


def test_consensus_pick_outweighs_solo() -> None:
    picks = [
        _pick("AAPL", "core", "claude"),
        _pick("AAPL", "core", "gpt"),  # consensus
        _pick("MSFT", "core", "claude"),  # solo
    ]
    portfolio = build_portfolio(picks)
    by_ticker = {h.ticker: h for h in portfolio}
    assert by_ticker["AAPL"].weight > by_ticker["MSFT"].weight


def test_moonshot_lower_weight_than_core() -> None:
    picks = [
        _pick("AAPL", "core", "claude"),
        _pick("MOON", "moonshot", "claude"),
    ]
    portfolio = build_portfolio(picks)
    by_ticker = {h.ticker: h for h in portfolio}
    assert by_ticker["AAPL"].weight > by_ticker["MOON"].weight


def test_nominated_by_populated() -> None:
    picks = [
        _pick("AAPL", "core", "claude"),
        _pick("AAPL", "core", "gpt"),
    ]
    portfolio = build_portfolio(picks)
    assert set(portfolio[0].nominated_by) == {"claude", "gpt"}


def test_sort_order_consensus_core_first() -> None:
    picks = [
        _pick("SOLO", "core", "claude"),
        _pick("CONSENSUS", "core", "claude"),
        _pick("CONSENSUS", "core", "gpt"),
        _pick("MOON", "moonshot", "claude"),
    ]
    portfolio = build_portfolio(picks)
    tickers = [h.ticker for h in portfolio]
    assert tickers.index("CONSENSUS") < tickers.index("SOLO")
    assert tickers.index("SOLO") < tickers.index("MOON")


def test_single_pick_portfolio() -> None:
    picks = [_pick("AAPL", "core", "claude")]
    portfolio = build_portfolio(picks)
    assert len(portfolio) == 1
    assert portfolio[0].weight == pytest.approx(100.0)
