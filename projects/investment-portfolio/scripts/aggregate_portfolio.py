"""
Aggregate brokerage cost basis CSVs into ticker,shares,avg_cost format.

Usage:
    python aggregate_portfolio.py <file.csv> [output.csv]

If no output file is given, prints to stdout.
"""

import csv
import io
import sys
import collections


def aggregate_csv(path: str) -> list[dict]:
    """Aggregate a cost basis CSV by ticker, computing weighted avg cost."""
    data = open(path).read().removeprefix("﻿")
    reader = csv.DictReader(io.StringIO(data))

    shares_total: dict[str, float] = collections.defaultdict(float)
    cost_total: dict[str, float] = collections.defaultdict(float)

    for row in reader:
        ticker = row["Symbol"].strip()
        shares = float(row["Shares"].strip())
        cost = float(row["CostBasis"].replace(",", "").strip())
        shares_total[ticker] += shares
        cost_total[ticker] += cost

    return [
        {
            "ticker": ticker,
            "shares": shares_total[ticker],
            "avg_cost": cost_total[ticker] / shares_total[ticker],
        }
        for ticker in sorted(shares_total)
    ]


def avg_cost_from_value(shares: float, current_value: float, total_gain_loss: float) -> float:
    """
    Derive avg_cost when you know current value and total unrealized gain/loss.

    cost_basis = current_value - total_gain_loss
    avg_cost   = cost_basis / shares
    """
    cost_basis = current_value - total_gain_loss
    return cost_basis / shares


def write_csv(rows: list[dict], dest=sys.stdout) -> None:
    writer = csv.DictWriter(dest, fieldnames=["ticker", "shares", "avg_cost"])
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "ticker": row["ticker"],
            "shares": f"{row['shares']:.6f}",
            "avg_cost": f"{row['avg_cost']:.4f}",
        })


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    rows = aggregate_csv(sys.argv[1])

    if len(sys.argv) >= 3:
        with open(sys.argv[2], "w", newline="") as f:
            write_csv(rows, f)
        print(f"Written to {sys.argv[2]}")
    else:
        write_csv(rows)
