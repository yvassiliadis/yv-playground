import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from google import genai
from openai import AsyncOpenAI

from .committee import claude_member, gemini_member, gpt_member

logger = logging.getLogger(__name__)
from .committee.aggregator import build_portfolio
from .config import EXCLUDED_TICKERS
from .enrichment import enrich_picks_with_prices
from .models import CommitteeRun, Pick, WebSource
from .screener import format_for_prompt, screen_universe

RUNS_DIR = Path(__file__).parent.parent / "data" / "runs"
_PICKS_CACHE_DIR = Path(__file__).parent.parent / "data" / "picks_cache"
_RUN_FILENAME_RE = re.compile(r"^\d{8}_\d{6}$")
_PICKS_CACHE_TTL_SECONDS = 24 * 3600
_RESEARCH_CACHE_TTL_SECONDS = 24 * 3600


def _load_picks_cache(member: str) -> tuple[list[Pick], list[WebSource]] | None:
    path = _PICKS_CACHE_DIR / f"{member}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        cached_at = datetime.fromisoformat(data["cached_at"])
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > _PICKS_CACHE_TTL_SECONDS:
            return None
        picks = [Pick.model_validate(p) for p in data["picks"]]
        sources = [WebSource.model_validate(s) for s in data.get("sources", [])]
        logger.info("Picks cache hit for %s (%d picks)", member, len(picks))
        return picks, sources
    except Exception:
        logger.debug("Failed to load picks cache for %s", member, exc_info=True)
        return None


def _load_research_cache() -> tuple[str, list[WebSource]] | None:
    path = _PICKS_CACHE_DIR / "research.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        cached_at = datetime.fromisoformat(data["cached_at"])
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > _RESEARCH_CACHE_TTL_SECONDS:
            return None
        sources = [WebSource.model_validate(s) for s in data.get("sources", [])]
        logger.info("Research cache hit (age %.0fs)", age)
        return data["research"], sources
    except Exception:
        logger.debug("Failed to load research cache", exc_info=True)
        return None


def _save_research_cache(research: str, sources: list[WebSource]) -> None:
    try:
        _PICKS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_PICKS_CACHE_DIR / "research.json").write_text(
            json.dumps(
                {
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "research": research,
                    "sources": [s.model_dump() for s in sources],
                },
                indent=2,
            )
        )
    except Exception:
        logger.warning("Failed to save research cache", exc_info=True)


def _save_picks_cache(member: str, picks: list[Pick], sources: list[WebSource]) -> None:
    try:
        _PICKS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_PICKS_CACHE_DIR / f"{member}.json").write_text(
            json.dumps(
                {
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "picks": [p.model_dump() for p in picks],
                    "sources": [s.model_dump() for s in sources],
                },
                indent=2,
            )
        )
    except Exception:
        logger.warning("Failed to save picks cache for %s", member, exc_info=True)


async def run_committee(
    anthropic_client: anthropic.AsyncAnthropic,
    openai_client: AsyncOpenAI,
    gemini_client: genai.Client,
) -> CommitteeRun:
    screened = await screen_universe()
    screened_section = format_for_prompt(screened)

    claude_cache = _load_picks_cache("claude")
    gpt_cache = _load_picks_cache("gpt")
    gemini_cache = _load_picks_cache("gemini")

    async def _claude() -> tuple[list[Pick], list[WebSource]]:
        if claude_cache:
            return claude_cache
        t0 = time.monotonic()
        research_cache = _load_research_cache()
        if research_cache:
            research, sources = research_cache
        else:
            research, sources = await claude_member.get_research(anthropic_client)
            _save_research_cache(research, sources)
        picks = await claude_member.get_picks(
            anthropic_client, screened_section, research
        )
        logger.info("Claude total: %.1fs", time.monotonic() - t0)
        _save_picks_cache("claude", picks, sources)
        return picks, sources

    async def _gpt() -> list[Pick]:
        if gpt_cache:
            return gpt_cache[0]
        picks = await gpt_member.get_picks(openai_client, screened_section)
        _save_picks_cache("gpt", picks, [])
        return picks

    async def _gemini() -> list[Pick]:
        if gemini_cache:
            return gemini_cache[0]
        picks = await gemini_member.get_picks(gemini_client, screened_section)
        _save_picks_cache("gemini", picks, [])
        return picks

    results = await asyncio.gather(_claude(), _gpt(), _gemini(), return_exceptions=True)

    claude_result, gpt_result, gemini_result = results

    if isinstance(claude_result, Exception):
        logger.warning(
            "Claude member failed — excluding from run", exc_info=claude_result
        )
        claude_picks, claude_sources = [], []
    else:
        claude_picks, claude_sources = claude_result

    if isinstance(gpt_result, Exception):
        logger.warning("GPT member failed — excluding from run", exc_info=gpt_result)
        gpt_picks = []
    else:
        gpt_picks = gpt_result

    if isinstance(gemini_result, Exception):
        logger.warning(
            "Gemini member failed — excluding from run", exc_info=gemini_result
        )
        gemini_picks = []
    else:
        gemini_picks = gemini_result

    if not claude_picks and not gpt_picks and not gemini_picks:
        raise RuntimeError("All committee members failed — cannot build portfolio")

    for member, picks in [
        ("claude", claude_picks),
        ("gpt", gpt_picks),
        ("gemini", gemini_picks),
    ]:
        if not picks:
            continue
        moonshots = [p for p in picks if p.conviction == "moonshot"]
        core = [p for p in picks if p.conviction == "core"]
        if len(moonshots) != 3:
            logger.warning(
                "%s returned %d moonshot(s); expected 3 — skipping member",
                member,
                len(moonshots),
            )
            if member == "claude":
                claude_picks = []
            elif member == "gpt":
                gpt_picks = []
            elif member == "gemini":
                gemini_picks = []
            continue
        if not (10 <= len(core) <= 25):
            logger.warning(
                "%s returned %d core pick(s); expected 10-25 — skipping member",
                member,
                len(core),
            )
            if member == "claude":
                claude_picks = []
            elif member == "gpt":
                gpt_picks = []
            elif member == "gemini":
                gemini_picks = []

    excluded_upper = {t.upper() for t in EXCLUDED_TICKERS}
    claude_picks = [p for p in claude_picks if p.ticker.upper() not in excluded_upper]
    gpt_picks = [p for p in gpt_picks if p.ticker.upper() not in excluded_upper]
    gemini_picks = [p for p in gemini_picks if p.ticker.upper() not in excluded_upper]

    all_picks = await enrich_picks_with_prices(claude_picks + gpt_picks + gemini_picks)
    claude_picks = [p for p in all_picks if p.member == "claude"]
    gpt_picks = [p for p in all_picks if p.member == "gpt"]
    gemini_picks = [p for p in all_picks if p.member == "gemini"]

    portfolio = build_portfolio(all_picks)

    run = CommitteeRun(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        claude_picks=claude_picks,
        gpt_picks=gpt_picks,
        gemini_picks=gemini_picks,
        portfolio=portfolio,
        claude_sources=claude_sources,
    )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_file = RUNS_DIR / f"{run.timestamp.strftime('%Y%m%d_%H%M%S')}.json"
    run_file.write_text(run.model_dump_json(indent=2))

    return run


def _filter_run(run: CommitteeRun) -> CommitteeRun:
    excluded = {t.upper() for t in EXCLUDED_TICKERS}

    portfolio = [
        h
        for h in run.portfolio
        if h.ticker.upper() not in excluded and len(h.nominated_by) >= 2
    ]
    claude_picks = [p for p in run.claude_picks if p.ticker.upper() not in excluded]
    gpt_picks = [p for p in run.gpt_picks if p.ticker.upper() not in excluded]
    gemini_picks = [p for p in run.gemini_picks if p.ticker.upper() not in excluded]

    if (
        len(portfolio) == len(run.portfolio)
        and len(claude_picks) == len(run.claude_picks)
        and len(gpt_picks) == len(run.gpt_picks)
        and len(gemini_picks) == len(run.gemini_picks)
        and all(len(h.nominated_by) >= 2 for h in run.portfolio)
    ):
        return run

    if len(portfolio) < len(run.portfolio) and portfolio:
        total = sum(h.weight for h in portfolio)
        if total > 0:
            scale = 100.0 / total
            portfolio = [
                h.model_copy(update={"weight": round(h.weight * scale, 2)})
                for h in portfolio
            ]

    return run.model_copy(
        update={
            "portfolio": portfolio,
            "claude_picks": claude_picks,
            "gpt_picks": gpt_picks,
            "gemini_picks": gemini_picks,
        }
    )


def load_latest_run() -> CommitteeRun | None:
    if not RUNS_DIR.exists():
        return None
    files = sorted(
        (f for f in RUNS_DIR.glob("*.json") if _RUN_FILENAME_RE.match(f.stem)),
        reverse=True,
    )
    if not files:
        return None
    data = json.loads(files[0].read_text())
    return _filter_run(CommitteeRun.model_validate(data))


def load_all_runs() -> list[CommitteeRun]:
    if not RUNS_DIR.exists():
        return []
    files = sorted(
        (f for f in RUNS_DIR.glob("*.json") if _RUN_FILENAME_RE.match(f.stem)),
        reverse=True,
    )
    runs = []
    for f in files:
        try:
            runs.append(
                _filter_run(CommitteeRun.model_validate(json.loads(f.read_text())))
            )
        except Exception:
            continue
    return runs
