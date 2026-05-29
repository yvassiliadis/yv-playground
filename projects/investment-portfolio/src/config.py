import json
from pathlib import Path

_EXCLUSIONS_FILE = Path(__file__).parent.parent / "data" / "exclusions.json"

EXCLUDED_TICKERS: set[str] = {
    "BRK.B",
    "DJT",
    "TSLA",
}

EXCLUDED_SECTORS: set[str] = {
    "Basic Materials",
    "Energy",
}

_DEFAULT_TICKERS: frozenset[str] = frozenset(EXCLUDED_TICKERS)
_DEFAULT_SECTORS: frozenset[str] = frozenset(EXCLUDED_SECTORS)


def load() -> None:
    if not _EXCLUSIONS_FILE.exists():
        return
    data = json.loads(_EXCLUSIONS_FILE.read_text())
    EXCLUDED_TICKERS.clear()
    EXCLUDED_TICKERS.update(data.get("tickers", _DEFAULT_TICKERS))
    EXCLUDED_SECTORS.clear()
    EXCLUDED_SECTORS.update(data.get("sectors", _DEFAULT_SECTORS))


def save() -> None:
    _EXCLUSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _EXCLUSIONS_FILE.write_text(
        json.dumps(
            {
                "tickers": sorted(EXCLUDED_TICKERS),
                "sectors": sorted(EXCLUDED_SECTORS),
            },
            indent=2,
        )
    )
