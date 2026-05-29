import json
import logging

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

from ..config import EXCLUDED_TICKERS
from ..models import Pick
from .philosophy import ADVISOR_PHILOSOPHY, MANDATE

SYSTEM_PROMPT = """You are a member of an investment committee. Your philosophy combines value investing (Buffett, Graham), growth investing (Fisher, Lynch), and disciplined momentum — applied with aggressive long-term conviction.

Before making picks, use Google Search to understand what's working in the market RIGHT NOW:
- Current macro environment and sector rotation
- Recent earnings season themes and surprises
- Any major catalysts or headwinds in the next 3-6 months

Do NOT search for individual stocks — the pre-screened list below is already fundamentals-qualified. Use search only to understand which sectors and themes have current momentum, then apply that context when selecting from the list. Each stock in the list includes its industry — use that to cross-reference your macro research against specific names (e.g. if cloud software has earnings momentum, favour stocks labelled Software—Application or Software—Infrastructure).

{mandate}

For each pick you MUST include:
1. A rationale grounded in your research
2. Variant perception — what does the market misunderstand or underestimate that creates an edge for a long-term holder?

Return ONLY valid JSON matching this schema, no markdown, no explanation:
{
  "core": [
    {
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "rationale": "...",
      "variant_perception": "Market underestimates services margin expansion..."
    }
  ],
  "moonshot": [
    {
      "ticker": "XYZ",
      "company_name": "XYZ Corp",
      "rationale": "...",
      "variant_perception": "..."
    }
  ]
}

Moonshot picks: exactly 3. Core picks: 10-20 (25 max).""".replace("{mandate}", MANDATE)

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


async def get_picks(client: genai.Client, screened_section: str = "") -> list[Pick]:
    excluded = ", ".join(sorted(EXCLUDED_TICKERS))
    system = SYSTEM_PROMPT.replace("{excluded_tickers}", excluded)
    if screened_section:
        system = system + "\n\n" + screened_section
    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents="Research current macro conditions and sector momentum using Google Search, then generate your best portfolio picks with variant perception for each.",
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            system_instruction=system,
            max_output_tokens=16384,
        ),
    )

    grounding = (
        response.candidates[0].grounding_metadata if response.candidates else None
    )
    search_used = bool(grounding and grounding.grounding_chunks)
    logger.info("Gemini search grounding used: %s", search_used)

    if not response.text:
        finish_reason = (
            response.candidates[0].finish_reason if response.candidates else "unknown"
        )
        raise RuntimeError(f"Gemini returned no text (finish_reason={finish_reason})")

    raw = response.text
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]
    try:
        data = json.loads(raw, strict=False)
    except json.JSONDecodeError as e:
        logger.error("Gemini JSON parse failed at char %d: %s\n...%s...", e.pos, e, raw[max(0, e.pos - 100):e.pos + 100])
        raise

    picks = []
    for p in data["core"]:
        picks.append(
            Pick(
                ticker=p["ticker"],
                company_name=p["company_name"],
                rationale=p["rationale"],
                conviction="core",
                member="gemini",
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
                member="gemini",
                variant_perception=p.get("variant_perception"),
            )
        )

    return picks


async def get_stock_opinion(
    client: genai.Client,
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
    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=ADVISOR_SYSTEM_PROMPT,
            response_mime_type="application/json",
            max_output_tokens=2048,
        ),
    )

    return json.loads(response.text)
