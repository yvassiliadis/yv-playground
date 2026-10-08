from .models import PortfolioHolding, PortfolioPosition, RebalancePlan, RebalanceTrade
from .tickers import canonical_ticker


def plan_rebalance(
    holdings: list[PortfolioPosition],
    targets: list[PortfolioHolding],
    prices: dict[str, float | None],
    amount: float,
    tax_rate: float,
    min_trade: float = 25.0,
) -> RebalancePlan:
    warnings: list[str] = []

    holdings_by_canon: dict[str, PortfolioPosition] = {}
    for pos in holdings:
        holdings_by_canon[canonical_ticker(pos.ticker)] = pos

    targets_by_canon: dict[str, PortfolioHolding] = {}
    for tgt in targets:
        targets_by_canon[canonical_ticker(tgt.ticker)] = tgt

    # Stable order: holdings first (in input order), then target-only tickers.
    order: list[str] = []
    seen: set[str] = set()
    for pos in holdings:
        canon = canonical_ticker(pos.ticker)
        if canon not in seen:
            seen.add(canon)
            order.append(canon)
    for tgt in targets:
        canon = canonical_ticker(tgt.ticker)
        if canon not in seen:
            seen.add(canon)
            order.append(canon)

    rows = []
    for canon in order:
        pos = holdings_by_canon.get(canon)
        tgt = targets_by_canon.get(canon)
        # Keep the ticker spelling actually held; fall back to the target's
        # spelling when the ticker isn't currently held.
        display_ticker = pos.ticker if pos is not None else tgt.ticker

        price = prices.get(display_ticker)
        if price is None:
            warnings.append(f"{display_ticker}: price unavailable, excluded from plan")
            continue

        shares_held = pos.shares if pos is not None else 0.0
        weight = tgt.weight if tgt is not None else 0.0
        current_value = shares_held * price
        target_value = amount * weight / 100.0

        rows.append(
            {
                "ticker": display_ticker,
                "company_name": tgt.company_name if tgt is not None else display_ticker,
                "price": price,
                "avg_cost": pos.avg_cost if pos is not None else None,
                "is_held": pos is not None,
                "shares_held": shares_held,
                "current_value": current_value,
                "target_value": target_value,
                "delta": target_value - current_value,
            }
        )

    current_total = sum(r["current_value"] for r in rows)
    target_total = sum(r["target_value"] for r in rows)

    for r in rows:
        delta = r["delta"]
        if abs(delta) < min_trade:
            r["action"] = "hold"
            r["trade_value"] = 0.0
        elif delta > 0:
            r["action"] = "buy"
            r["trade_value"] = delta
        else:
            r["action"] = "sell"
            r["trade_value"] = delta

    total_sells = sum(-r["trade_value"] for r in rows if r["action"] == "sell")
    cash_withdrawn = current_total - amount
    target_buy_sum = total_sells - cash_withdrawn
    raw_buy_sum = sum(r["trade_value"] for r in rows if r["action"] == "buy")

    if target_buy_sum < 0:
        warnings.append(
            "not enough proceeds from sells to cover buys; buys clamped to 0"
        )
        scale = 0.0
    elif raw_buy_sum > 0:
        scale = target_buy_sum / raw_buy_sum
    else:
        scale = 1.0

    for r in rows:
        if r["action"] == "buy":
            r["trade_value"] = r["trade_value"] * scale
            if r["trade_value"] == 0.0:
                r["action"] = "hold"

    for r in rows:
        r["shares"] = abs(r["trade_value"]) / r["price"] if r["action"] != "hold" else 0.0

    net_realized_gain = 0.0
    for r in rows:
        if r["action"] != "sell":
            continue
        if r["avg_cost"] is None:
            r["realized_gain"] = None
            r["est_tax"] = None
            warnings.append(f"{r['ticker']}: avg_cost unknown, tax estimate excluded")
            continue
        realized_gain = r["shares"] * (r["price"] - r["avg_cost"])
        r["realized_gain"] = realized_gain
        r["est_tax"] = max(0.0, realized_gain) * tax_rate
        net_realized_gain += realized_gain

    est_tax = max(0.0, net_realized_gain) * tax_rate

    full_liquidation_gain = 0.0
    for r in rows:
        if r["is_held"] and r["avg_cost"] is not None:
            full_liquidation_gain += r["shares_held"] * (r["price"] - r["avg_cost"])
    full_liquidation_tax = max(0.0, full_liquidation_gain) * tax_rate

    trades = [
        RebalanceTrade(
            ticker=r["ticker"],
            company_name=r["company_name"],
            action=r["action"],
            current_value=round(r["current_value"], 2),
            target_value=round(r["target_value"], 2),
            trade_value=round(r["trade_value"], 2),
            shares=r["shares"],
            realized_gain=(
                round(r["realized_gain"], 2)
                if r.get("realized_gain") is not None
                else None
            ),
            est_tax=(
                round(r["est_tax"], 2) if r.get("est_tax") is not None else None
            ),
        )
        for r in rows
    ]

    total_buys = sum(r["trade_value"] for r in rows if r["action"] == "buy")

    return RebalancePlan(
        trades=trades,
        current_total=round(current_total, 2),
        target_total=round(target_total, 2),
        cash_withdrawn=round(cash_withdrawn, 2),
        total_buys=round(total_buys, 2),
        total_sells=round(total_sells, 2),
        est_tax=round(est_tax, 2),
        full_liquidation_tax=round(full_liquidation_tax, 2),
        warnings=warnings,
    )
