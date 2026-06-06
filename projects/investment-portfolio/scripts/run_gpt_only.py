import argparse
import asyncio
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from openai import AsyncOpenAI

from src.committee import gpt_member
from src.committee.aggregator import build_portfolio
from src.enrichment import enrich_picks_with_prices
from src.models import CommitteeRun
from src.screener import format_for_prompt, screen_universe

load_dotenv()

RUNS_DIR = Path("data/runs")


async def main(limit: int | None = None) -> None:
    client = AsyncOpenAI()

    print("Screening universe for fundamentals...")
    screened = await screen_universe()
    if limit:
        screened = screened[:limit]
    screened_section = format_for_prompt(screened)
    print(
        f"  {len(screened)} stocks total ({sum(1 for s in screened if s.tier == 'suggestion')} suggestions, {sum(1 for s in screened if s.tier == 'opportunity')} opportunities)"
    )

    t1 = time.monotonic()
    print("Asking GPT for picks...")
    picks = await gpt_member.get_picks(client, screened_section)
    print(f"[GPT done] {time.monotonic() - t1:.1f}s")

    moonshots = [p for p in picks if p.conviction == "moonshot"]
    core = [p for p in picks if p.conviction == "core"]
    print(f"  Got {len(core)} core picks, {len(moonshots)} moonshots")

    if len(moonshots) != 3:
        raise ValueError(f"gpt returned {len(moonshots)} moonshot(s); expected 3")
    if not (10 <= len(core) <= 25):
        raise ValueError(f"gpt returned {len(core)} core pick(s); expected 10-25")

    print("Enriching picks with yfinance prices...")
    picks = await enrich_picks_with_prices(picks)

    portfolio = build_portfolio(picks)

    run = CommitteeRun(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        claude_picks=[],
        gpt_picks=picks,
        gemini_picks=[],
        portfolio=portfolio,
    )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_file = RUNS_DIR / f"{run.timestamp.strftime('%Y%m%d_%H%M%S')}.json"
    run_file.write_text(run.model_dump_json(indent=2))
    print(f"Saved to {run_file}")
    print(f"Portfolio: {len(portfolio)} holdings")


parser = argparse.ArgumentParser()
parser.add_argument(
    "--limit",
    type=int,
    default=None,
    help="Cap the screened stock list for quick tests",
)
args = parser.parse_args()
asyncio.run(main(limit=args.limit))
