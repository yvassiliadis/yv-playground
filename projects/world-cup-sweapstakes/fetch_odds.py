# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx",
#     "matplotlib",
#     "numpy",
#     "python-dotenv",
#     "scikit-learn",
# ]
# ///

import json
import os
import sys
from pathlib import Path

import httpx
import matplotlib.pyplot as plt
import numpy as np
from dotenv import load_dotenv
from sklearn.cluster import KMeans

ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup_winner/odds"
CACHE_FILE = Path("wc2026_odds.json")

# Confirmed WC 2026 qualified nations (official names)
CONFIRMED_TEAMS = {
    # CONCACAF
    "Canada", "Mexico", "United States", "Haiti", "Panama", "Curaçao",
    # AFC
    "Australia", "Iran", "Japan", "Jordan", "Qatar", "Saudi Arabia",
    "South Korea", "Uzbekistan", "Iraq",
    # CAF
    "Algeria", "Cape Verde", "DR Congo", "Egypt", "Ghana", "Ivory Coast",
    "Morocco", "Senegal", "South Africa", "Tunisia",
    # CONMEBOL
    "Argentina", "Brazil", "Colombia", "Ecuador", "Paraguay", "Uruguay",
    # OFC
    "New Zealand",
    # UEFA
    "Austria", "Belgium", "Bosnia & Herzegovina", "Croatia", "Czechia",
    "England", "France", "Germany", "Netherlands", "Norway", "Portugal",
    "Scotland", "Spain", "Sweden", "Switzerland", "Türkiye",
}

# Odds API names → official tournament names
TEAM_NAME_MAP = {
    "USA": "United States",
    "Czech Republic": "Czechia",
    "Turkey": "Türkiye",
    "Ivory Coast": "Ivory Coast",
}


def fetch_odds(api_key: str) -> list[dict]:
    params = {
        "apiKey": api_key,
        "markets": "outrights",
        "regions": "uk",
        "oddsFormat": "decimal",
    }
    response = httpx.get(ODDS_API_URL, params=params, timeout=10)
    if not response.is_success:
        print(f"API error {response.status_code}: {response.text}")
        response.raise_for_status()
    return response.json()


def average_odds_per_team(raw: list[dict]) -> dict[str, float]:
    """Average decimal odds across all bookmakers for confirmed WC teams only."""
    totals: dict[str, list[float]] = {}
    for event in raw:
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market["key"] != "outrights":
                    continue
                for outcome in market.get("outcomes", []):
                    name = TEAM_NAME_MAP.get(outcome["name"], outcome["name"])
                    if name not in CONFIRMED_TEAMS:
                        continue
                    totals.setdefault(name, []).append(float(outcome["price"]))
    missing = CONFIRMED_TEAMS - set(totals.keys())
    if missing:
        print(f"Warning: no odds found for {missing}")
    return {team: sum(prices) / len(prices) for team, prices in totals.items()}


def assign_tiers(team_odds: dict[str, float], n_tiers: int = 4) -> dict[int, list[str]]:
    """Use k-means on log-odds to find natural tier boundaries."""
    teams = list(team_odds.keys())
    # Log scale: compresses the long tail of long-shots for better clustering
    log_odds = np.log(np.array([team_odds[t] for t in teams])).reshape(-1, 1)

    kmeans = KMeans(n_clusters=n_tiers, random_state=42, n_init=10)
    labels = kmeans.fit_predict(log_odds)

    # Sort cluster labels so tier 1 = favorites (lowest odds)
    centers = kmeans.cluster_centers_.flatten()
    rank = np.argsort(centers)
    remap = {old: new + 1 for new, old in enumerate(rank)}

    tiers: dict[int, list[str]] = {i + 1: [] for i in range(n_tiers)}
    for team, label in zip(teams, labels):
        tiers[remap[label]].append(team)

    for tier in tiers.values():
        tier.sort(key=lambda t: team_odds[t])

    return tiers


def plot_distribution(team_odds: dict[str, float], tiers: dict[int, list[str]]) -> None:
    teams_sorted = sorted(team_odds, key=lambda t: team_odds[t])
    odds_sorted = [team_odds[t] for t in teams_sorted]

    tier_colors = {1: "#e63946", 2: "#f4a261", 3: "#2a9d8f", 4: "#457b9d"}
    team_tier = {t: tier for tier, members in tiers.items() for t in members}

    colors = [tier_colors[team_tier[t]] for t in teams_sorted]

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.bar(range(len(teams_sorted)), odds_sorted, color=colors)
    ax.set_xticks(range(len(teams_sorted)))
    ax.set_xticklabels(teams_sorted, rotation=90, fontsize=7)
    ax.set_ylabel("Average decimal odds (lower = favourite)")
    ax.set_title("FIFA World Cup 2026 — Team odds by tier")

    for tier, color in tier_colors.items():
        ax.bar(0, 0, color=color, label=f"Tier {tier}")
    ax.legend()

    plt.tight_layout()
    plt.savefig("tier_distribution.png", dpi=150)
    print("Plot saved to tier_distribution.png")


def print_tiers(tiers: dict[int, list[str]], team_odds: dict[str, float]) -> None:
    labels = {1: "Favorites", 2: "Strong", 3: "Mid-tier", 4: "Long-shots"}
    for tier, teams in tiers.items():
        print(f"\nTier {tier} — {labels[tier]} ({len(teams)} teams):")
        for team in teams:
            print(f"  {team:<30} {team_odds[team]:.1f}x")


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("Set ODDS_API_KEY environment variable first.")
        print("Get a free key at https://the-odds-api.com")
        sys.exit(1)

    if CACHE_FILE.exists():
        print(f"Loading cached odds from {CACHE_FILE}")
        raw = json.loads(CACHE_FILE.read_text())
    else:
        print("Fetching odds from The Odds API...")
        raw = fetch_odds(api_key)
        CACHE_FILE.write_text(json.dumps(raw, indent=2))
        print(f"Saved to {CACHE_FILE}")

    team_odds = average_odds_per_team(raw)
    print(f"\nFound {len(team_odds)} teams")

    tiers = assign_tiers(team_odds)
    print_tiers(tiers, team_odds)

    tiers_output = {str(k): v for k, v in tiers.items()}
    Path("tiers.json").write_text(json.dumps(tiers_output, indent=2))
    print("\nTiers saved to tiers.json")

    plot_distribution(team_odds, tiers)


if __name__ == "__main__":
    main()
