# Estimated API Cost Per Committee Run

Estimates based on a Claude-only test run (~10 web searches, ~60K accumulated input tokens across the agentic loop).

## With Web Search Enabled (All 3 Members)

| Member | Search calls | Search cost | Token cost | Est. per run |
|--------|-------------|------------|------------|-------------|
| Claude Opus 4.7 | ~10 × $0.010 | $0.10 | 60K in × $5/M + 5K out × $25/M = $0.43 | **~$0.53** |
| GPT-4o | ~10 × $0.030 | $0.30 | 60K in × $2.50/M + 5K out × $10/M = $0.20 | **~$0.50** |
| Gemini 2.0 Flash | ~10 × $0.035 | $0.35 | 60K in × $0.10/M + 5K out × $0.40/M = $0.01 | **~$0.36** |
| **Total** | | | | **~$1.40** |

## Current State (GPT/Gemini Without Web Search)

Single API call each, no search loop:

| Member | Est. per run |
|--------|-------------|
| Claude Opus 4.7 (with web search) | ~$0.53 |
| GPT-4o (no web search) | ~$0.05 |
| Gemini 2.0 Flash (no web search) | ~$0.01 |
| **Total** | **~$0.60** |

Adding web search to GPT + Gemini roughly doubles the per-run cost: ~$0.60 → ~$1.40.

## Caveats

- **Search call count is the biggest variable** — Claude made ~10 calls in the test run but could range 5–15 depending on how much the model decides to research. GPT/Gemini may behave differently.
- **Token accumulation** drives Claude's cost — each continuation turn in the agentic loop includes the full conversation history including prior search results.
- **GPT search** ($30/1000) is the priciest of the three, via the Responses API `web_search_preview` tool.
- **Gemini grounding** has a free tier (1,500 requests/day on Google AI Studio free tier); on Vertex paid tier it's ~$35/1000.
- **ETF picks** have no analyst targets from yfinance; the upside multiplier defaults to 1.0 for them — no cost impact.

## Usage Context

At ~$1.40/run running once a week: **~$6/month**. Running daily: **~$42/month**.
