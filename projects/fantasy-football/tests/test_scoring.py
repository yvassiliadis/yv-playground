"""Tests for ffdraft.scoring."""

from pathlib import Path

import polars as pl

from ffdraft import scoring

RULES = {
    "passing yard": 0.04,
    "passing td": 6.0,
    "reception": 0.5,
    "receiving yard": 0.1,
}

REPO_ROOT = Path(__file__).resolve().parents[1]


def _long_row(season, week, source, player_id, stat_name, stat_value):
    return {
        "season": season,
        "week": week,
        "source": source,
        "player_id": player_id,
        "stat_name": stat_name,
        "stat_value": stat_value,
    }


def test_load_scoring_rules_parses_real_csv():
    rules = scoring.load_scoring_rules(REPO_ROOT / "data" / "scoring_rules.csv")

    assert len(rules) == 47
    assert rules["passing yard"] == 0.04
    assert rules["passing td"] == 6.0
    assert rules["0-39 FG made"] == 3.0
    assert rules["0-13 pts allowed"] == 0.0
    assert rules["fumble lost"] == -2.0


def test_score_matches_hand_computed_total():
    # mahomes: 300 passing yards, 3 passing tds -> 300*0.04 + 3*6 = 12 + 18 = 30
    # kelce: 6 receptions, 80 receiving yards -> 6*0.5 + 80*0.1 = 3 + 8 = 11
    df = pl.DataFrame(
        [
            _long_row(2023, 1, "actuals", "mahomes", "passing yard", 300.0),
            _long_row(2023, 1, "actuals", "mahomes", "passing td", 3.0),
            _long_row(2023, 1, "actuals", "kelce", "reception", 6.0),
            _long_row(2023, 1, "actuals", "kelce", "receiving yard", 80.0),
        ]
    )

    result = scoring.score(df, RULES)

    totals = dict(zip(result["player_id"], result["fantasy_points"]))
    assert totals == {"mahomes": 30.0, "kelce": 11.0}


def test_score_drops_unmapped_stat_without_crashing_or_affecting_others():
    # mahomes has an unmapped stat ("two-point dance") mixed in with a mapped
    # one; kelce has only mapped stats and must be unaffected by mahomes's
    # unmapped row.
    df = pl.DataFrame(
        [
            _long_row(2023, 1, "actuals", "mahomes", "passing yard", 100.0),
            _long_row(2023, 1, "actuals", "mahomes", "two-point dance", 999.0),
            _long_row(2023, 1, "actuals", "kelce", "reception", 4.0),
        ]
    )

    result = scoring.score(df, RULES)

    totals = dict(zip(result["player_id"], result["fantasy_points"]))
    # mahomes: 100 * 0.04 = 4.0, the unmapped stat contributes nothing
    assert totals == {"mahomes": 4.0, "kelce": 2.0}


def test_score_treats_all_sources_identically():
    df = pl.DataFrame(
        [
            _long_row(2023, 1, "actuals", "mahomes", "passing yard", 300.0),
            _long_row(2023, 1, "actuals", "mahomes", "passing td", 3.0),
            _long_row(2023, 1, "some_source", "mahomes", "passing yard", 300.0),
            _long_row(2023, 1, "some_source", "mahomes", "passing td", 3.0),
        ]
    )

    result = scoring.score(df, RULES)

    totals = {
        row["source"]: row["fantasy_points"] for row in result.iter_rows(named=True)
    }
    assert totals == {"actuals": 30.0, "some_source": 30.0}
