import pytest

from src.models import PortfolioHolding, PortfolioPosition
from src.rebalance import plan_rebalance


def make_target(ticker, weight, company_name=None):
    return PortfolioHolding(
        ticker=ticker,
        company_name=company_name or ticker,
        conviction="core",
        weight=weight,
        nominated_by=["claude"],
        rationale="test",
    )


def trades_by_ticker(plan):
    return {t.ticker: t for t in plan.trades}


def test_overlap_only_sells_delta_and_buys_new_names():
    holdings = [
        PortfolioPosition(ticker="NVDA", shares=1.0),
        PortfolioPosition(ticker="AAPL", shares=1.0),
    ]
    targets = [
        make_target("NVDA", 40),  # target$ = 8
        make_target("GOOG", 25),  # target$ = 5
        make_target("META", 35),  # target$ = 7
        # AAPL absent from the new run entirely
    ]
    prices = {"NVDA": 10.0, "AAPL": 10.0, "GOOG": 1.0, "META": 1.0}

    plan = plan_rebalance(
        holdings, targets, prices, amount=20.0, tax_rate=0.2, min_trade=1.0
    )
    trades = trades_by_ticker(plan)

    assert trades["NVDA"].action == "sell"
    assert trades["NVDA"].trade_value == pytest.approx(-2.0)
    assert trades["AAPL"].action == "sell"
    assert trades["AAPL"].trade_value == pytest.approx(-10.0)
    assert trades["AAPL"].target_value == pytest.approx(0.0)
    assert trades["GOOG"].action == "buy"
    assert trades["GOOG"].trade_value == pytest.approx(5.0)
    assert trades["META"].action == "buy"
    assert trades["META"].trade_value == pytest.approx(7.0)
    assert plan.total_sells == pytest.approx(plan.total_buys)


def test_exit_of_ticker_not_in_new_run_is_full_sell():
    holdings = [PortfolioPosition(ticker="ZETA", shares=2.0)]
    targets = []
    prices = {"ZETA": 5.0}

    plan = plan_rebalance(
        holdings, targets, prices, amount=0.0, tax_rate=0.2, min_trade=1.0
    )
    trade = plan.trades[0]

    assert trade.ticker == "ZETA"
    assert trade.action == "sell"
    assert trade.target_value == pytest.approx(0.0)
    assert trade.trade_value == pytest.approx(-10.0)
    assert trade.shares == pytest.approx(2.0)


def test_min_trade_skip_scales_remaining_buys():
    holdings = [
        PortfolioPosition(ticker="AAA", shares=70.0),
        PortfolioPosition(ticker="BBB", shares=40.0),
    ]
    targets = [
        make_target("AAA", 40),  # target$ = 40, current 70 -> delta -30 (real sell)
        make_target("BBB", 30),  # target$ = 30, current 40 -> delta -10 (below min_trade)
        make_target("CCC", 30),  # target$ = 30, current 0 -> delta +30 (raw buy)
    ]
    prices = {"AAA": 1.0, "BBB": 1.0, "CCC": 2.0}

    plan = plan_rebalance(
        holdings, targets, prices, amount=100.0, tax_rate=0.2, min_trade=25.0
    )
    trades = trades_by_ticker(plan)

    assert trades["BBB"].action == "hold"
    assert trades["BBB"].trade_value == pytest.approx(0.0)
    assert trades["BBB"].shares == pytest.approx(0.0)
    assert plan.cash_withdrawn == pytest.approx(10.0)
    assert plan.total_buys == pytest.approx(plan.total_sells - plan.cash_withdrawn)


def test_withdrawal_reduces_cash_and_keeps_plan_self_funded():
    holdings = [PortfolioPosition(ticker="AAA", shares=10.0)]
    targets = [make_target("AAA", 100)]  # target$ = 80 at amount 80
    prices = {"AAA": 10.0}

    plan = plan_rebalance(
        holdings, targets, prices, amount=80.0, tax_rate=0.2, min_trade=5.0
    )

    assert plan.cash_withdrawn == pytest.approx(20.0)
    assert plan.total_buys == pytest.approx(plan.total_sells - plan.cash_withdrawn)


def test_googl_googl_alias_is_single_trade_keeping_held_spelling():
    holdings = [PortfolioPosition(ticker="GOOGL", shares=1.0)]
    targets = [make_target("GOOG", 90)]  # target$ = 90 at amount 100
    prices = {"GOOGL": 100.0}

    plan = plan_rebalance(
        holdings, targets, prices, amount=100.0, tax_rate=0.2, min_trade=1.0
    )

    assert len(plan.trades) == 1
    trade = plan.trades[0]
    assert trade.ticker == "GOOGL"
    assert trade.action == "sell"
    assert trade.trade_value == pytest.approx(-10.0)


def test_loss_nets_against_gain_in_tax_estimate():
    holdings = [
        PortfolioPosition(ticker="AAA", shares=10.0, avg_cost=5.0),  # gain
        PortfolioPosition(ticker="BBB", shares=10.0, avg_cost=20.0),  # loss
    ]
    targets = []  # full liquidation
    prices = {"AAA": 10.0, "BBB": 10.0}

    plan = plan_rebalance(
        holdings, targets, prices, amount=0.0, tax_rate=0.2, min_trade=1.0
    )
    trades = trades_by_ticker(plan)

    assert trades["AAA"].realized_gain == pytest.approx(50.0)
    assert trades["BBB"].realized_gain == pytest.approx(-100.0)
    # net realized gain is -50 -> floored to 0 before applying the tax rate
    assert plan.est_tax == pytest.approx(0.0)


def test_missing_avg_cost_excludes_ticker_from_tax_but_keeps_trade():
    holdings = [PortfolioPosition(ticker="AAA", shares=10.0)]  # avg_cost unknown
    targets = []
    prices = {"AAA": 10.0}

    plan = plan_rebalance(
        holdings, targets, prices, amount=0.0, tax_rate=0.2, min_trade=1.0
    )
    trade = plan.trades[0]

    assert trade.action == "sell"
    assert trade.realized_gain is None
    assert trade.est_tax is None
    assert any("avg_cost unknown" in w for w in plan.warnings)
    assert plan.est_tax == pytest.approx(0.0)


def test_missing_price_excludes_ticker_from_trades_and_totals():
    holdings = [
        PortfolioPosition(ticker="AAA", shares=10.0),
        PortfolioPosition(ticker="BBB", shares=10.0),
    ]
    targets = [make_target("BBB", 100)]
    prices = {"AAA": None, "BBB": 10.0}

    plan = plan_rebalance(
        holdings, targets, prices, amount=100.0, tax_rate=0.2, min_trade=1.0
    )

    tickers = {t.ticker for t in plan.trades}
    assert "AAA" not in tickers
    assert any("AAA" in w and "price unavailable" in w for w in plan.warnings)


def test_held_ticker_not_in_run_sells_and_new_ticker_buys():
    holdings = [PortfolioPosition(ticker="ZETA", shares=5.0)]
    targets = [make_target("CORR", 100)]
    prices = {"ZETA": 10.0, "CORR": 10.0}

    plan = plan_rebalance(
        holdings, targets, prices, amount=50.0, tax_rate=0.2, min_trade=1.0
    )
    trades = trades_by_ticker(plan)

    assert trades["ZETA"].action == "sell"
    assert trades["ZETA"].target_value == pytest.approx(0.0)
    assert trades["CORR"].action == "buy"
    assert trades["CORR"].current_value == pytest.approx(0.0)


def test_full_liquidation_tax_exceeds_partial_sell_tax():
    holdings = [PortfolioPosition(ticker="AAA", shares=10.0, avg_cost=2.0)]
    targets = [make_target("AAA", 90)]  # target$ = 90, current 100 -> partial sell
    prices = {"AAA": 10.0}

    plan = plan_rebalance(
        holdings, targets, prices, amount=100.0, tax_rate=0.2, min_trade=1.0
    )

    assert plan.est_tax == pytest.approx(1.6)
    assert plan.full_liquidation_tax == pytest.approx(16.0)
    assert plan.full_liquidation_tax > plan.est_tax
