from src.models import PortfolioPosition, TrackedPortfolio


def test_position_without_avg_cost():
    pos = PortfolioPosition(ticker="AAPL", shares=10.0)
    assert pos.avg_cost is None
    assert pos.ticker == "AAPL"


def test_portfolio_round_trips_json():
    portfolio = TrackedPortfolio(
        name="My Portfolio",
        positions=[
            PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=150.0),
            PortfolioPosition(ticker="VTI", shares=50.0),
        ],
    )
    data = portfolio.model_dump()
    restored = TrackedPortfolio.model_validate(data)
    assert restored.name == "My Portfolio"
    assert len(restored.positions) == 2
    assert restored.positions[0].avg_cost == 150.0
    assert restored.positions[1].avg_cost is None
