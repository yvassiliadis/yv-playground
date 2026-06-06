import asyncio
import logging
import time

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
from dotenv import load_dotenv

from src.committee import claude_member
from src.screener import ScreenedStock, format_for_prompt

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

TICKERS = [
    ScreenedStock(
        "NVDA", "NVIDIA Corporation", gross_margin=0.74, roe=0.35, tier="suggestion"
    ),
    ScreenedStock(
        "MSFT", "Microsoft Corporation", gross_margin=0.70, roe=0.38, tier="suggestion"
    ),
    ScreenedStock(
        "GOOG", "Alphabet Inc.", gross_margin=0.57, roe=0.30, tier="suggestion"
    ),
    ScreenedStock(
        "META", "Meta Platforms", gross_margin=0.81, roe=0.36, tier="suggestion"
    ),
    ScreenedStock(
        "AMZN", "Amazon.com Inc.", gross_margin=0.49, roe=0.22, tier="suggestion"
    ),
    ScreenedStock(
        "CRM", "Salesforce Inc.", gross_margin=0.77, roe=0.11, tier="suggestion"
    ),
    ScreenedStock("ADBE", "Adobe Inc.", gross_margin=0.88, roe=0.36, tier="suggestion"),
    ScreenedStock(
        "NOW", "ServiceNow Inc.", gross_margin=0.79, roe=0.31, tier="suggestion"
    ),
    ScreenedStock(
        "PANW", "Palo Alto Networks", gross_margin=0.74, roe=0.20, tier="suggestion"
    ),
    ScreenedStock(
        "PLTR",
        "Palantir Technologies",
        gross_margin=0.80,
        roe=-0.05,
        tier="opportunity",
    ),
]


async def main() -> None:
    client = anthropic.AsyncAnthropic()
    section = format_for_prompt(TICKERS)

    print(f"Screened universe ({len(TICKERS)} tickers):\n{section}\n")
    print("Running Claude picks with web search...\n")

    t0 = time.monotonic()
    picks, sources = await claude_member.get_picks(client, section)
    elapsed = time.monotonic() - t0

    core = [p for p in picks if p.conviction == "core"]
    moonshots = [p for p in picks if p.conviction == "moonshot"]

    print(f"\n{'─' * 50}")
    print(f"  Total time : {elapsed:.1f}s")
    print(f"  Picks      : {len(core)} core, {len(moonshots)} moonshots")
    print(f"  Sources    : {len(sources)}")
    if sources:
        for s in sources:
            print(f"    - {s.title}  ({s.url})")


asyncio.run(main())
