import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .models import AdvisorResponse

LOG_PATH = Path(__file__).parent.parent / "data" / "advisor_log.json"
CSV_PATH = Path(__file__).parent.parent / "data" / "advisor_log.csv"

_CSV_FIELDS = [
    "timestamp",
    "ticker",
    "company_name",
    "recommendation",
    "fits_philosophy",
    "suggested_allocation_pct",
    "mean_upside_pct",
    "median_upside_pct",
    "claude_take",
    "gpt_take",
    "gemini_take",
    "claude_rec",
    "gpt_rec",
    "gemini_rec",
]


def load() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    with LOG_PATH.open() as f:
        return json.load(f)


def append(response: AdvisorResponse) -> None:
    entries = load()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": response.ticker,
        "company_name": response.company_name,
        "recommendation": response.recommendation,
        "fits_philosophy": response.fits_philosophy,
        "suggested_allocation_pct": response.suggested_allocation_pct,
        "mean_upside_pct": response.mean_upside_pct,
        "median_upside_pct": response.median_upside_pct,
        "claude_take": response.claude_take,
        "gpt_take": response.gpt_take,
        "gemini_take": response.gemini_take,
        "claude_rec": response.claude_rec,
        "gpt_rec": response.gpt_rec,
        "gemini_rec": response.gemini_rec,
    }
    entries.append(entry)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w") as f:
        json.dump(entries, f, indent=2)

    with CSV_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(entries)
