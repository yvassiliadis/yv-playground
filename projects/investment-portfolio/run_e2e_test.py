"""End-to-end test using a small hardcoded screened list instead of Finviz.

Run from outside Claude Code (safe-chain proxy blocks Anthropic API):
    uv run python run_e2e_test.py
"""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from google import genai

from src.committee import claude_member, gemini_member
from src.committee.aggregator import build_portfolio
from src.enrichment import enrich_picks_with_prices
from src.models import CommitteeRun, WebSource
from src.screener import ScreenedStock, format_for_prompt

load_dotenv()

RUNS_DIR = Path("data/runs")

TEST_STOCKS = [
    ScreenedStock(
        "NVDA",
        "NVIDIA Corporation",
        "Semiconductors",
        0.75,
        1.23,
        0.90,
        0.57,
        "May 28 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "MSFT",
        "Microsoft Corporation",
        "Software—Infrastructure",
        0.70,
        0.38,
        0.32,
        0.45,
        "Jul 30 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "GOOGL",
        "Alphabet Inc.",
        "Internet Content & Info",
        0.57,
        0.32,
        0.25,
        0.32,
        "Jul 29 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "META",
        "Meta Platforms Inc.",
        "Internet Content & Info",
        0.81,
        0.37,
        0.31,
        0.42,
        "Jul 30 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "ADBE",
        "Adobe Inc.",
        "Software—Application",
        0.88,
        0.37,
        0.28,
        0.36,
        "Jun 12 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "NOW",
        "ServiceNow Inc.",
        "Software—Application",
        0.79,
        0.28,
        0.22,
        0.24,
        "Jul 23 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "INTU",
        "Intuit Inc.",
        "Software—Application",
        0.79,
        0.25,
        0.19,
        0.23,
        "Aug 20 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "CRM",
        "Salesforce Inc.",
        "Software—Application",
        0.77,
        0.25,
        0.18,
        0.22,
        "May 28 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "CRWD",
        "CrowdStrike Holdings",
        "Software—Infrastructure",
        0.75,
        0.22,
        0.20,
        0.21,
        "Jun 3 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "AXON",
        "Axon Enterprise Inc.",
        "Aerospace & Defense",
        0.63,
        0.28,
        0.23,
        0.19,
        "Aug 5 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "TTD",
        "The Trade Desk Inc.",
        "Software—Application",
        0.81,
        0.24,
        0.18,
        0.20,
        "May 7 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "MELI",
        "MercadoLibre Inc.",
        "Internet Retail",
        0.55,
        0.21,
        0.18,
        0.14,
        "Aug 6 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "V",
        "Visa Inc.",
        "Credit Services",
        0.81,
        0.48,
        0.41,
        0.67,
        "Jul 22 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "MA",
        "Mastercard Inc.",
        "Credit Services",
        0.79,
        0.21,
        0.45,
        0.58,
        "Jul 31 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "COST",
        "Costco Wholesale Corp.",
        "Discount Stores",
        0.13,
        0.32,
        0.28,
        0.07,
        "Sep 25 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "HUBS",
        "HubSpot Inc.",
        "Software—Application",
        0.84,
        0.10,
        0.08,
        0.12,
        "Aug 6 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "DDOG",
        "Datadog Inc.",
        "Software—Infrastructure",
        0.81,
        0.22,
        0.19,
        0.23,
        "May 6 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "ZS",
        "Zscaler Inc.",
        "Software—Infrastructure",
        0.79,
        0.20,
        0.18,
        0.21,
        "Jun 2 AMC",
        "suggestion",
    ),
    ScreenedStock(
        "SNOW",
        "Snowflake Inc.",
        "Software—Application",
        0.70,
        None,
        0.15,
        0.03,
        "May 28 AMC",
        "opportunity",
    ),
    ScreenedStock(
        "RBLX",
        "Roblox Corporation",
        "Electronic Gaming & Multimedia",
        0.72,
        None,
        None,
        None,
        "Aug 13 AMC",
        "opportunity",
    ),
]


async def main() -> None:
    anthropic_client = anthropic.AsyncAnthropic()
    gemini_client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    screened_section = format_for_prompt(TEST_STOCKS)
    print(f"Test universe: {len(TEST_STOCKS)} stocks")
    print(screened_section[:400], "...\n")

    claude_picks: list = []
    claude_sources: list = []
    gemini_picks: list = []

    _RESEARCH_CACHE = Path("data/picks_cache/research.json")
    _RESEARCH_CACHE_TTL = 4 * 3600

    async def _claude() -> None:
        nonlocal claude_picks, claude_sources
        t_total = time.monotonic()
        try:
            # Research phase (cached)
            research = ""
            claude_sources = []
            if _RESEARCH_CACHE.exists():
                try:
                    data = json.loads(_RESEARCH_CACHE.read_text())
                    cached_at = datetime.fromisoformat(data["cached_at"])
                    age = (datetime.now(timezone.utc) - cached_at).total_seconds()
                    if age < _RESEARCH_CACHE_TTL:
                        research = data["research"]
                        claude_sources = [WebSource(**s) for s in data.get("sources", [])]
                        print(f"[Research cache hit] age {age:.0f}s, {len(claude_sources)} sources")
                except Exception:
                    pass

            if not research:
                t = time.monotonic()
                research, claude_sources = await claude_member.get_research(anthropic_client)
                print(f"[Research done] {time.monotonic() - t:.1f}s — {len(claude_sources)} sources")
                _RESEARCH_CACHE.parent.mkdir(parents=True, exist_ok=True)
                _RESEARCH_CACHE.write_text(json.dumps({
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "research": research,
                    "sources": [s.model_dump() for s in claude_sources],
                }, indent=2))

            # Picks phase
            t = time.monotonic()
            claude_picks = await claude_member.get_picks(
                anthropic_client, screened_section, research
            )
            core = len([p for p in claude_picks if p.conviction == "core"])
            moonshots = len([p for p in claude_picks if p.conviction == "moonshot"])
            print(f"[Picks done] {time.monotonic() - t:.1f}s — {core} core, {moonshots} moonshots")
            print(f"[Claude done] {time.monotonic() - t_total:.1f}s total, {len(claude_sources)} sources")
        except Exception as e:
            print(f"[Claude FAILED] {e}")

    async def _gemini() -> None:
        nonlocal gemini_picks
        t = time.monotonic()
        try:
            gemini_picks = await gemini_member.get_picks(
                gemini_client, screened_section
            )
            core = len([p for p in gemini_picks if p.conviction == "core"])
            moonshots = len([p for p in gemini_picks if p.conviction == "moonshot"])
            print(
                f"[Gemini done] {time.monotonic() - t:.1f}s — {core} core, {moonshots} moonshots"
            )
        except Exception as e:
            print(f"[Gemini FAILED] {e}")

    print("Asking committee (parallel)...")
    t0 = time.monotonic()
    await asyncio.gather(_claude(), _gemini())
    print(f"[Committee done] {time.monotonic() - t0:.1f}s total")

    all_picks = claude_picks + gemini_picks
    if not all_picks:
        print("All members failed — cannot build portfolio")
        return

    print("Enriching picks with prices...")
    all_picks = await enrich_picks_with_prices(all_picks)
    claude_picks = [p for p in all_picks if p.member == "claude"]
    gemini_picks = [p for p in all_picks if p.member == "gemini"]

    portfolio = build_portfolio(all_picks)

    run = CommitteeRun(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        claude_picks=claude_picks,
        gpt_picks=[],
        gemini_picks=gemini_picks,
        portfolio=portfolio,
        claude_sources=claude_sources,
    )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_file = RUNS_DIR / f"{run.timestamp.strftime('%Y%m%d_%H%M%S')}.json"
    run_file.write_text(run.model_dump_json(indent=2))

    print(f"\nSaved: {run_file}")
    print(f"Portfolio: {len(portfolio)} holdings")
    for h in sorted(portfolio, key=lambda x: -x.weight):
        members = "+".join(h.nominated_by)
        print(f"  {h.ticker:6} {h.weight:5.1f}%  [{members}]  {h.company_name}")


asyncio.run(main())
