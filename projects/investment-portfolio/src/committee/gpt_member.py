import json
import logging
import re
import time

from openai import AsyncOpenAI

from ..config import EXCLUDED_TICKERS
from ..models import Pick
from .philosophy import ADVISOR_PHILOSOPHY, MANDATE

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a member of an investment committee. Your philosophy combines value investing (Buffett, Graham), growth investing (Fisher, Lynch), and disciplined momentum — applied with aggressive long-term conviction.

Before making picks, use web search to understand what's working in the market RIGHT NOW:
- Current macro environment and sector rotation
- Recent earnings season themes and surprises
- Any major catalysts or headwinds in the next 3-6 months

Do NOT search for individual stocks — the pre-screened list below is already fundamentals-qualified. Use search only to understand which sectors and themes have current momentum, then apply that context when selecting from the list. Each stock in the list includes its industry — use that to cross-reference your macro research against specific names (e.g. if cloud software has earnings momentum, favour stocks labelled Software—Application or Software—Infrastructure).

{mandate}

For each pick provide:
1. A rationale grounded in your research (2-3 sentences max)
2. Variant perception — what does the market misunderstand or underestimate that creates an edge for a long-term holder? (1-2 sentences max)

Moonshot picks: exactly 3. Core picks: 10-20 (25 max).""".replace("{mandate}", MANDATE)

PICKS_SCHEMA = {
    "type": "object",
    "properties": {
        "core": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "company_name": {"type": "string"},
                    "rationale": {"type": "string"},
                    "variant_perception": {"type": "string"},
                },
                "required": [
                    "ticker",
                    "company_name",
                    "rationale",
                    "variant_perception",
                ],
                "additionalProperties": False,
            },
        },
        "moonshot": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "company_name": {"type": "string"},
                    "rationale": {"type": "string"},
                    "variant_perception": {"type": "string"},
                },
                "required": [
                    "ticker",
                    "company_name",
                    "rationale",
                    "variant_perception",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["core", "moonshot"],
    "additionalProperties": False,
}

ADVISOR_SYSTEM_PROMPT = """\
You are a member of an investment committee evaluating a specific stock.
{advisor_philosophy}

Be direct and opinionated. Return ONLY valid JSON:
{
  "company_name": "...",
  "recommendation": "buy" | "pass" | "watch",
  "fits_philosophy": true | false,
  "take": "2-3 sentence honest opinion on this stock",
  "suggested_allocation_pct": 5.0
}

suggested_allocation_pct: if recommendation is "buy", suggest a portfolio allocation percentage (0-100) considering the current portfolio composition provided. If "pass", return 0. If "watch", return a small placeholder like 1-2.""".replace("{advisor_philosophy}", ADVISOR_PHILOSOPHY)


_CITATION_RE = re.compile(r"\s*\(\[[^\]]*\]\(https?://[^)]+\)\)")


def _strip_citations(text: str | None) -> str | None:
    if text is None:
        return None
    return _CITATION_RE.sub("", text).strip()


async def get_picks(client: AsyncOpenAI, screened_section: str = "") -> list[Pick]:
    excluded = ", ".join(sorted(EXCLUDED_TICKERS))
    system = SYSTEM_PROMPT.replace("{excluded_tickers}", excluded)
    if screened_section:
        system = system + "\n\n" + screened_section

    t0 = time.monotonic()
    response = await client.responses.create(
        model="gpt-5",
        instructions=system,
        input="Research current macro conditions and sector momentum using web search, then generate your best portfolio picks with variant perception for each.",
        tools=[{"type": "web_search_preview"}],
        text={
            "format": {
                "type": "json_schema",
                "name": "portfolio_picks",
                "schema": PICKS_SCHEMA,
                "strict": True,
            }
        },
    )

    search_used = any(
        getattr(item, "type", None) == "web_search_call" for item in response.output
    )
    logger.info("GPT picks: web_search=%s, %.1fs", search_used, time.monotonic() - t0)

    data = json.loads(response.output_text)

    seen: set[str] = set()
    picks = []
    for conviction, entries in [("core", data["core"]), ("moonshot", data["moonshot"])]:
        for p in entries:
            ticker = p["ticker"]
            if ticker in seen:
                continue
            seen.add(ticker)
            picks.append(
                Pick(
                    ticker=ticker,
                    company_name=p["company_name"],
                    rationale=_strip_citations(p["rationale"]),
                    conviction=conviction,
                    member="gpt",
                    variant_perception=_strip_citations(p.get("variant_perception")),
                )
            )
    return picks


async def get_stock_opinion(
    client: AsyncOpenAI,
    ticker: str,
    fundamentals: str = "",
    portfolio_context: str = "",
) -> dict:
    content = (
        f"What do you think about investing in {ticker}? Give me your honest opinion."
    )
    if fundamentals:
        content = f"{content}\n\n{fundamentals}"
    if portfolio_context:
        content = f"{content}\n\n{portfolio_context}"
    response = await client.chat.completions.create(
        model="gpt-5.4-mini",
        max_completion_tokens=4096,
        reasoning_effort="low",
        messages=[
            {"role": "system", "content": ADVISOR_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        response_format={"type": "json_object"},
    )

    choice = response.choices[0]
    msg = choice.message
    if msg.refusal:
        raise ValueError(f"GPT refused: {msg.refusal}")
    if not msg.content:
        raise ValueError(f"GPT returned empty content (finish_reason={choice.finish_reason!r})")
    return json.loads(msg.content)
