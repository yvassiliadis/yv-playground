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


# ---------------------------------------------------------------------------
# Repeated-snapshot dedupe (C1).
# ---------------------------------------------------------------------------


def _snapshot_row(snapshot_date, stat_name, stat_value, source="sleeper"):
    return {
        "season": 2025,
        "week": 0,
        "source": source,
        "snapshot_date": snapshot_date,
        "player_id": "mahomes",
        "stat_name": stat_name,
        "stat_value": stat_value,
    }


def test_score_does_not_double_count_two_snapshots_of_the_same_season():
    """Two `ingest snapshot` runs for one season/source must not double EP."""
    one_run = pl.DataFrame(
        [
            _snapshot_row("2025-08-01", "passing yard", 4000.0),
            _snapshot_row("2025-08-01", "passing td", 30.0),
        ]
    )
    two_runs = pl.concat(
        [
            one_run,
            pl.DataFrame(
                [
                    _snapshot_row("2025-08-08", "passing yard", 4000.0),
                    _snapshot_row("2025-08-08", "passing td", 30.0),
                ]
            ),
        ]
    )

    single = scoring.score(one_run, RULES)["fantasy_points"][0]
    doubled = scoring.score(two_runs, RULES)["fantasy_points"][0]

    assert single == 4000.0 * 0.04 + 30.0 * 6.0
    assert doubled == single


def test_score_uses_the_latest_snapshot_not_the_first():
    df = pl.DataFrame(
        [
            _snapshot_row("2025-08-01", "passing yard", 4000.0),
            _snapshot_row("2025-08-08", "passing yard", 5000.0),
        ]
    )

    assert scoring.score(df, RULES)["fantasy_points"][0] == 5000.0 * 0.04


def test_latest_snapshot_rows_is_per_source_not_global():
    """A stale source keeps its own newest snapshot; it is not wiped out by a
    fresher snapshot from a different source."""
    df = pl.DataFrame(
        [
            _snapshot_row("2025-08-01", "passing yard", 4000.0, source="sleeper"),
            _snapshot_row("2025-08-01", "passing yard", 3000.0, source="espn"),
            _snapshot_row("2025-08-08", "passing yard", 3500.0, source="espn"),
        ]
    )

    totals = {
        row["source"]: row["fantasy_points"]
        for row in scoring.score(df, RULES).iter_rows(named=True)
    }
    assert totals == {"sleeper": 4000.0 * 0.04, "espn": 3500.0 * 0.04}


def test_latest_snapshot_rows_no_ops_without_a_snapshot_date_column():
    df = pl.DataFrame(
        [
            _long_row(2023, 1, "actuals", "mahomes", "passing yard", 100.0),
            _long_row(2023, 1, "actuals", "mahomes", "passing yard", 100.0),
        ]
    )

    assert scoring.latest_snapshot_rows(df).height == 2
