from ..models import Pick, PortfolioHolding


def _upside_multiplier(mean_upside: float | None, median_upside: float | None) -> float:
    """
    Scales raw weight by analyst upside signal.
    Uses average of mean and median when both available.
    Range: [0.7, 1.5] so upside adjusts weight by at most ±30-50% without
    overriding the nomination-count signal.
    """
    if mean_upside is not None and median_upside is not None:
        upside = (mean_upside + median_upside) / 2
    elif mean_upside is not None:
        upside = mean_upside
    elif median_upside is not None:
        upside = median_upside
    else:
        return 1.0
    return max(0.7, min(1.5, 1.0 + upside / 100.0 * 0.5))


def build_portfolio(all_picks: list[Pick]) -> list[PortfolioHolding]:
    """
    Aggregates picks from all committee members into a weighted portfolio.
    Weight scales with nomination count (1x/2x/3x) and analyst upside signal.
    Moonshots carry half the base weight of core picks.
    """
    by_ticker: dict[str, list[Pick]] = {}
    for pick in all_picks:
        key = pick.ticker.upper()
        by_ticker.setdefault(key, [])
        # Keep one pick per member per ticker; prefer core over moonshot
        existing = next((p for p in by_ticker[key] if p.member == pick.member), None)
        if existing is None:
            by_ticker[key].append(pick)
        elif existing.conviction == "moonshot" and pick.conviction == "core":
            by_ticker[key].remove(existing)
            by_ticker[key].append(pick)

    by_ticker = {t: picks for t, picks in by_ticker.items() if len(picks) >= 2}

    raw_weights: dict[str, float] = {}
    for ticker, picks in by_ticker.items():
        conviction = picks[0].conviction
        base = 1.0 if conviction == "core" else 0.5
        upside_mult = _upside_multiplier(
            picks[0].mean_upside_pct, picks[0].median_upside_pct
        )
        raw_weights[ticker] = base * float(len(picks)) * upside_mult

    total = sum(raw_weights.values())

    holdings = []
    for ticker, picks in by_ticker.items():
        weight = round((raw_weights[ticker] / total) * 100, 2)
        enriched = next((p for p in picks if p.current_price is not None), picks[0])
        holdings.append(
            PortfolioHolding(
                ticker=ticker,
                company_name=picks[0].company_name,
                conviction=picks[0].conviction,
                weight=weight,
                nominated_by=[p.member for p in picks],
                rationale=picks[0].rationale,
                current_price=enriched.current_price,
                mean_upside_pct=enriched.mean_upside_pct,
                median_upside_pct=enriched.median_upside_pct,
            )
        )

    # Sort: consensus core first, then solo core, then moonshots
    holdings.sort(
        key=lambda h: (h.conviction == "moonshot", len(h.nominated_by) == 1, -h.weight)
    )
    return holdings
