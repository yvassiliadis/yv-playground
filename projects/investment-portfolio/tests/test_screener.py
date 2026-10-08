from unittest.mock import MagicMock, patch

from src.screener import ScreenedStock, add_held_tickers, format_for_prompt


def _mock_ticker_info(info_by_ticker: dict[str, dict | str]):
    def _make_ticker(ticker: str) -> MagicMock:
        info = info_by_ticker.get(ticker)
        if info is None:
            raise AssertionError(f"no mock info configured for {ticker}")
        if info == "raise":
            raise RuntimeError("yfinance boom")
        mock = MagicMock()
        mock.info = info
        return mock

    return _make_ticker


def _suggestion(ticker: str) -> ScreenedStock:
    return ScreenedStock(
        ticker=ticker,
        company_name=f"{ticker} Inc",
        industry="Software",
        gross_margin=0.6,
        roe=0.3,
        roic=0.2,
        operating_margin=0.25,
        earnings_date=None,
        tier="suggestion",
    )


async def test_held_ticker_missing_from_screen_is_appended_as_additional():
    info_by_ticker = {
        "NVDA": {
            "longName": "NVIDIA Corporation",
            "industry": "Semiconductors",
            "sector": "Technology",
            "returnOnEquity": 0.9,
            "grossMargins": 0.75,
            "operatingMargins": 0.5,
        }
    }
    with patch("src.screener.yf.Ticker", side_effect=_mock_ticker_info(info_by_ticker)):
        result = await add_held_tickers([], ["NVDA"])

    assert len(result) == 1
    added = result[0]
    assert added.ticker == "NVDA"
    assert added.tier == "additional"
    assert added.company_name == "NVIDIA Corporation"
    assert added.industry == "Semiconductors"
    assert added.roe == 0.9
    assert added.gross_margin == 0.75
    assert added.operating_margin == 0.5


async def test_already_screened_ticker_not_duplicated_via_alias():
    stocks = [_suggestion("GOOG")]
    with patch("src.screener.yf.Ticker") as mock_ticker:
        result = await add_held_tickers(stocks, ["GOOGL"])

    mock_ticker.assert_not_called()
    assert result == stocks
    assert len(result) == 1


async def test_manually_excluded_ticker_not_added(monkeypatch):
    # src/screener.py does `from .config import EXCLUDED_TICKERS`, binding its
    # own module-level name to the same set object — so pin that name
    # directly (not src.config.EXCLUDED_TICKERS) to a known fixture value,
    # rather than relying on config.py's current/default state, which
    # config.load() may have already overwritten in place from the real
    # (gitignored) data/exclusions.json if another test (e.g. test_api.py,
    # via `import api`) ran first this session.
    monkeypatch.setattr("src.screener.EXCLUDED_TICKERS", {"TSLA"})
    with patch("src.screener.yf.Ticker") as mock_ticker:
        result = await add_held_tickers([], ["TSLA"])

    mock_ticker.assert_not_called()
    assert result == []


async def test_manually_excluded_sector_not_added(monkeypatch):
    monkeypatch.setattr("src.screener.EXCLUDED_SECTORS", {"Energy"})
    info_by_ticker = {
        "XOM": {
            "longName": "Exxon Mobil Corporation",
            "industry": "Oil & Gas",
            "sector": "Energy",
            "returnOnEquity": 0.2,
            "grossMargins": 0.4,
            "operatingMargins": 0.3,
        }
    }
    with patch("src.screener.yf.Ticker", side_effect=_mock_ticker_info(info_by_ticker)):
        result = await add_held_tickers([], ["XOM"])

    assert result == []


async def test_yfinance_fetch_failure_still_adds_ticker_with_minimal_fields():
    info_by_ticker = {"BADTICKER": "raise"}
    with patch("src.screener.yf.Ticker", side_effect=_mock_ticker_info(info_by_ticker)):
        result = await add_held_tickers([], ["BADTICKER"])

    assert len(result) == 1
    added = result[0]
    assert added.ticker == "BADTICKER"
    assert added.tier == "additional"
    assert added.company_name == "BADTICKER"
    assert added.industry is None
    assert added.gross_margin is None
    assert added.roe is None
    assert added.roic is None
    assert added.operating_margin is None
    assert added.earnings_date is None


async def test_yfinance_falsy_info_still_adds_ticker_with_minimal_fields():
    info_by_ticker = {"EMPTYINFO": {}}
    with patch("src.screener.yf.Ticker", side_effect=_mock_ticker_info(info_by_ticker)):
        result = await add_held_tickers([], ["EMPTYINFO"])

    assert len(result) == 1
    added = result[0]
    assert added.ticker == "EMPTYINFO"
    assert added.tier == "additional"
    assert added.company_name == "EMPTYINFO"


async def test_add_held_tickers_does_not_mutate_input_list():
    stocks = [_suggestion("AAPL")]
    info_by_ticker = {
        "MSFT": {
            "longName": "Microsoft Corporation",
            "industry": "Software",
            "sector": "Technology",
            "returnOnEquity": 0.4,
            "grossMargins": 0.7,
            "operatingMargins": 0.4,
        }
    }
    with patch("src.screener.yf.Ticker", side_effect=_mock_ticker_info(info_by_ticker)):
        result = await add_held_tickers(stocks, ["MSFT"])

    assert stocks == [_suggestion("AAPL")]
    assert len(result) == 2


def test_format_for_prompt_renders_additional_section_when_present():
    stocks = [
        _suggestion("AAPL"),
        ScreenedStock(
            ticker="NVDA",
            company_name="NVIDIA Corporation",
            industry="Semiconductors",
            gross_margin=0.75,
            roe=0.9,
            roic=None,
            operating_margin=0.5,
            earnings_date=None,
            tier="additional",
        ),
    ]
    text = format_for_prompt(stocks)

    assert "ADDITIONAL — not from the screen, eligible picks judged on merits:" in text
    assert "NVDA (NVIDIA Corporation)" in text
    assert text.index("ADDITIONAL") > text.index("SUGGESTIONS")


def test_format_for_prompt_omits_additional_section_when_absent():
    stocks = [_suggestion("AAPL")]
    text = format_for_prompt(stocks)

    assert "ADDITIONAL" not in text
