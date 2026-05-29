import asyncio
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google import genai

from src.committee import gemini_member
from src.committee.aggregator import build_portfolio
from src.enrichment import enrich_picks_with_prices
from src.models import CommitteeRun
from src.screener import format_for_prompt, screen_universe

load_dotenv()

RUNS_DIR = Path("data/runs")


async def main() -> None:
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    print("Screening universe for fundamentals...")
    screened = await screen_universe()
    screened_section = format_for_prompt(screened)
    print(
        f"  {len(screened)} stocks total ({sum(1 for s in screened if s.tier == 'suggestion')} suggestions, {sum(1 for s in screened if s.tier == 'opportunity')} opportunities)"
    )

    t1 = time.monotonic()
    print("Asking Gemini for picks...")
    picks = await gemini_member.get_picks(client, screened_section)
    print(f"[Gemini done] {time.monotonic() - t1:.1f}s")

    moonshots = [p for p in picks if p.conviction == "moonshot"]
    core = [p for p in picks if p.conviction == "core"]
    print(f"  Got {len(core)} core picks, {len(moonshots)} moonshots")

    if len(moonshots) != 3:
        raise ValueError(f"gemini returned {len(moonshots)} moonshot(s); expected 3")
    if not (10 <= len(core) <= 25):
        raise ValueError(f"gemini returned {len(core)} core pick(s); expected 10-25")

    print("Enriching picks with yfinance prices...")
    picks = await enrich_picks_with_prices(picks)

    portfolio = build_portfolio(picks)

    run = CommitteeRun(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        claude_picks=[],
        gpt_picks=[],
        gemini_picks=picks,
        portfolio=portfolio,
    )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_file = RUNS_DIR / f"{run.timestamp.strftime('%Y%m%d_%H%M%S')}.json"
    run_file.write_text(run.model_dump_json(indent=2))
    print(f"Saved to {run_file}")
    print(f"Portfolio: {len(portfolio)} holdings")


asyncio.run(main())
