import json
import logging
import time

import anthropic

logger = logging.getLogger(__name__)

from ..config import EXCLUDED_TICKERS
from ..models import Pick, WebSource
from .philosophy import ADVISOR_PHILOSOPHY, MANDATE

RESEARCH_SYSTEM_PROMPT = """You are a financial research analyst preparing a market briefing for an investment committee focused on high-quality growth stocks on US exchanges.

Search for current information and provide a concise briefing covering:
- Current macro environment and sector rotation
- Recent earnings season themes and surprises
- Major catalysts and headwinds for the next 3-6 months

Be specific about which sectors and industries have momentum or headwinds."""

SYSTEM_PROMPT = """You are a member of an investment committee. Your philosophy combines value investing (Buffett, Graham), growth investing (Fisher, Lynch), and disciplined momentum — applied with aggressive long-term conviction.

Current market research is provided below — use it to understand which sectors and themes have current momentum. Each stock in the pre-screened list includes its industry label — cross-reference the research against specific names (e.g. if cloud software has earnings momentum, favour stocks labelled Software—Application or Software—Infrastructure).

{mandate}

For each pick you MUST include:
1. A rationale grounded in your research (2-3 sentences max)
2. Variant perception — what does the market misunderstand or underestimate that creates an edge for a long-term holder? (1-2 sentences max)

Return ONLY valid JSON matching this schema, no markdown, no explanation:
{{
  "core": [
    {{
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "rationale": "...",
      "variant_perception": "Market underestimates services margin expansion..."
    }}
  ],
  "moonshot": [
    {{
      "ticker": "XYZ",
      "company_name": "XYZ Corp",
      "rationale": "...",
      "variant_perception": "..."
    }}
  ]
}}

Moonshot picks: exactly 3. Core picks: 10-20 (25 max).""".replace("{mandate}", MANDATE)

WEB_SEARCH_MAX_USES = 2

WEB_SEARCH_DOMAINS: list[str] = [
    "benzinga.com",
    "finance.yahoo.com",
    "fool.com",
    "investorplace.com",
    "kiplinger.com",
    "marketbeat.com",
    "nasdaq.com",
    "schwab.com",
    "stockanalysis.com",
]

WEB_SEARCH_BLOCKED_DOMAINS: list[str] = [
    "barrons.com",
    "bloomberg.com",
    "economist.com",
    "ft.com",
    "fool.com/premium",
    "fool.com/services",
    "gurufocus.com",
    "investors.com",
    "morningstar.com",
    "pro.thestreet.com",
    "research.investors.com",
    "seekingalpha.com",
    "thestreet.com",
    "valueline.com",
    "valuelinelibrary.com",
    "wsj.com",
    "zacks.com",
]

ADVISOR_SYSTEM_PROMPT = """\
You are a member of an investment committee evaluating a specific stock.
{advisor_philosophy}

Be direct and opinionated. Return ONLY valid JSON:
{{
  "company_name": "...",
  "recommendation": "buy" | "pass" | "watch",
  "fits_philosophy": true | false,
  "take": "2-3 sentence honest opinion on this stock",
  "suggested_allocation_pct": 5.0
}}

suggested_allocation_pct: if recommendation is "buy", suggest a portfolio allocation percentage (0-100) considering the current portfolio composition provided. If "pass", return 0. If "watch", return a small placeholder like 1-2.""".replace("{advisor_philosophy}", ADVISOR_PHILOSOPHY)


def _extract_sources(content_blocks: list) -> list[WebSource]:
    sources = []
    seen_urls: set[str] = set()
    for block in content_blocks:
        if getattr(block, "type", None) == "tool_result":
            for item in getattr(block, "content", []):
                if getattr(item, "type", None) == "web_search_result":
                    url = getattr(item, "url", None)
                    title = getattr(item, "title", None)
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        sources.append(WebSource(url=url, title=title or url))
    return sources


def _parse_picks(response) -> list[Pick]:
    raw = next(
        block.text
        for block in reversed(response.content)
        if hasattr(block, "text") and block.type == "text"
    )
    # Strip markdown code fences if the model added them despite instructions
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]
    data = json.loads(raw)

    picks = []
    for p in data["core"]:
        picks.append(
            Pick(
                ticker=p["ticker"],
                company_name=p["company_name"],
                rationale=p["rationale"],
                conviction="core",
                member="claude",
                variant_perception=p.get("variant_perception"),
            )
        )
    for p in data["moonshot"]:
        picks.append(
            Pick(
                ticker=p["ticker"],
                company_name=p["company_name"],
                rationale=p["rationale"],
                conviction="moonshot",
                member="claude",
                variant_perception=p.get("variant_perception"),
            )
        )
    return picks


async def get_research(client: anthropic.AsyncAnthropic) -> tuple[str, list[WebSource]]:
    t0 = time.monotonic()
    messages: list[dict] = [
        {
            "role": "user",
            "content": "Search for current macro conditions, sector rotation, recent earnings themes, and key catalysts and headwinds for the next 3-6 months. Provide a concise briefing for an investment committee.",
        }
    ]
    all_content_blocks: list = []
    response = None
    container_id: str | None = None

    while True:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=RESEARCH_SYSTEM_PROMPT,
            tools=[
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": WEB_SEARCH_MAX_USES,
                    "blocked_domains": WEB_SEARCH_BLOCKED_DOMAINS,
                }
            ],
            messages=messages,
            timeout=600.0,
            **({"container_id": container_id} if container_id else {}),
        )
        container_id = getattr(response, "container_id", None) or container_id
        all_content_blocks.extend(response.content)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "pause_turn":
            break

    search_count = sum(
        1
        for block in all_content_blocks
        if getattr(block, "type", None) == "tool_use"
        and getattr(block, "name", None) == "web_search"
    )
    research_text = next(
        (
            block.text
            for block in reversed(response.content)
            if hasattr(block, "text") and block.type == "text"
        ),
        "",
    )
    sources = _extract_sources(all_content_blocks)
    logger.info(
        "Research: %d searches, %d sources in %.1fs",
        search_count,
        len(sources),
        time.monotonic() - t0,
    )
    return research_text, sources


async def get_picks(
    client: anthropic.AsyncAnthropic, screened_section: str = "", research: str = ""
) -> list[Pick]:
    excluded = ", ".join(sorted(EXCLUDED_TICKERS))
    system = SYSTEM_PROMPT.replace("{excluded_tickers}", excluded)
    if research:
        system = system + "\n\n## Current Market Research\n\n" + research
    if screened_section:
        system = system + "\n\n" + screened_section

    t0 = time.monotonic()
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[
            {
                "role": "user",
                "content": "Based on the market research and screened stock list provided, generate your best portfolio picks with variant perception for each.",
            }
        ],
        timeout=120.0,
    )
    logger.info("Picks generation: %.1fs", time.monotonic() - t0)
    return _parse_picks(response)


async def get_stock_opinion(
    client: anthropic.AsyncAnthropic,
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
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=ADVISOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    raw = message.content[0].text
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"No JSON object in Claude advisor response for {ticker}")
    return json.loads(raw[start:end])
