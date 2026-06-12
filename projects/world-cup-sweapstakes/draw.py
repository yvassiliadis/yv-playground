# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "python-dotenv",
# ]
# ///

import argparse
import csv
import json
import random
from pathlib import Path


def load_tiers(path: Path) -> dict[int, list[str]]:
    raw = json.loads(path.read_text())
    return {int(k): v for k, v in raw.items()}


def build_pool(teams: list[str], n_participants: int) -> list[str]:
    """Each team assigned as evenly as possible across participants."""
    base, extra = divmod(n_participants, len(teams))
    return teams * base + teams[:extra]


def draw(
    participants: list[str],
    tiers: dict[int, list[str]],
    seed: int,
) -> dict[str, dict[str, str]]:
    rng = random.Random(seed)

    tier_pools: dict[int, list[str]] = {}
    for tier, teams in tiers.items():
        pool = build_pool(teams, len(participants))
        rng.shuffle(pool)
        tier_pools[tier] = pool

    assignments: dict[str, dict[str, str]] = {}
    for i, participant in enumerate(participants):
        assignments[participant] = {
            f"tier_{tier}": tier_pools[tier][i] for tier in sorted(tiers)
        }

    return assignments


def print_assignments(
    assignments: dict[str, dict[str, str]], tiers: dict[int, list[str]]
) -> None:
    tier_labels = {1: "Favorites", 2: "Strong", 3: "Mid-tier", 4: "Long-shots"}
    header = f"{'Participant':<25}" + "".join(
        f"  {tier_labels[t]:<22}" for t in sorted(tiers)
    )
    print(header)
    print("-" * len(header))
    for participant, teams in sorted(assignments.items()):
        row = f"{participant:<25}" + "".join(
            f"  {teams[f'tier_{t}']:<22}" for t in sorted(tiers)
        )
        print(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="World Cup sweepstakes draw")
    parser.add_argument("--participants", type=Path, default=Path("participants.txt"))
    parser.add_argument("--tiers", type=Path, default=Path("tiers.json"))
    parser.add_argument("--seed", type=int, required=True, help="RNG seed for reproducibility")
    parser.add_argument("--output", type=Path, default=Path("assignments.json"))
    args = parser.parse_args()

    participants = [
        line.strip()
        for line in args.participants.read_text().splitlines()
        if line.strip()
    ]
    tiers = load_tiers(args.tiers)

    print(f"Drawing for {len(participants)} participants, seed={args.seed}\n")
    assignments = draw(participants, tiers, args.seed)

    portfolios = [tuple(sorted(teams.values())) for teams in assignments.values()]
    if len(portfolios) != len(set(portfolios)):
        print(f"ERROR: duplicate portfolios detected with seed={args.seed}. Try a different seed.")
        raise SystemExit(1)

    print_assignments(assignments, tiers)

    args.output.write_text(json.dumps(assignments, indent=2, ensure_ascii=False))
    print(f"\nSaved to {args.output}")

    csv_path = args.output.with_suffix(".csv")
    tier_labels = {1: "Favorites", 2: "Strong", 3: "Mid-tier", 4: "Long-shots"}
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Participant"] + [tier_labels[t] for t in sorted(tiers)])
        for participant, teams in sorted(assignments.items()):
            writer.writerow([participant] + [teams[f"tier_{t}"] for t in sorted(tiers)])
    print(f"Saved to {csv_path}")


if __name__ == "__main__":
    main()
