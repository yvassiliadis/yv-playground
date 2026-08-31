"""Assemble the draft board: blend -> VOR (+ alt-VORs) -> consistency, one row per player.

`build_board` is the one function this module exports. It orchestrates three
already-built pieces into the single ranked table a drafter actually reads:

* `model.blend.apply_blend` -- one blended `EP` (expected points) row per
  player for the season being drafted.
* `metrics.vor.compute_vor` plus its four independently-callable alt-VOR
  siblings (`risk_adjusted_vor`, `dropoff_value`, `roster_math_replacement`,
  `positional_zscore`) -- `VOR` and three sanity-check/alternative-ranking
  columns alongside it.
* `metrics.consistency.compute_consistency` -- `stddev`/`floor`/`ceiling`/
  `pct_weeks_above_baseline` from historical weekly actuals.

ADP plumbing
------------
No earlier task in this project ingests ADP (average draft position) data --
there is no `ingest/adp.py` and no canonical ADP source. `compute_vor` and
`dropoff_value` both need one (to size `N_pos` and to threshold "picks
ahead" respectively), and the board's final `adp` column needs one too. This
module therefore takes `adp` as a plain `pl.DataFrame` argument (`player_id`,
`position`, `adp`) rather than fetching it -- the caller (currently `cli.py`)
is responsible for sourcing it, e.g. from a manually-maintained CSV, until a
real ADP ingestion task exists.

Column semantics
-----------------
The plan's board spec names three "alt-VORs": risk-adjusted, dropoff, and
roster-math z-score. `metrics/vor.py` actually exposes four independent
alt-metrics (`risk_adjusted_vor`, `dropoff_value`, `roster_math_vor`,
`positional_zscore`) -- roster-math replacement and positional z-score are
two distinct sanity-check columns, not one hyphenated "roster-math z-score"
column. This board includes all four rather than dropping one, since the
plan's own module (`vor.py`'s docstring) treats all four as the alt-VOR set
`board.py` is meant to surface.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ffdraft.config import DEFAULT_ROSTER_CONFIG, TEAMS, RosterConfig
from ffdraft.metrics.consistency import compute_consistency
from ffdraft.metrics.vor import (
    DEFAULT_LAMBDA_RISK,
    compute_vor,
    dropoff_value,
    positional_zscore,
    risk_adjusted_vor,
    roster_math_replacement,
)
from ffdraft.model.blend import apply_blend

#: Final board columns, in display order. `rank` is assigned from the
#: `vor` descending sort (the plan's stated primary sort key); every other
#: column is a straight rename/select off the upstream frames.
BOARD_COLUMNS = [
    "rank",
    "player",
    "pos",
    "team",
    "ep",
    "vor",
    "risk_adjusted_vor",
    "dropoff_value",
    "roster_math_vor",
    "positional_zscore",
    "stddev",
    "floor",
    "ceiling",
    "pct_weeks_above_baseline",
    "adp",
]


def build_board(
    projections: pl.DataFrame,
    weights_path: Path,
    adp: pl.DataFrame,
    weekly_actuals: pl.DataFrame,
    consistency_seasons: list[int],
    rules: dict[str, float],
    teams: int = TEAMS,
    roster_config: RosterConfig = DEFAULT_ROSTER_CONFIG,
    lambda_risk: float = DEFAULT_LAMBDA_RISK,
    picks_ahead: int = 10,
) -> pl.DataFrame:
    """Build the ranked draft board.

    `projections`/`weights_path`/`rules` feed `blend.apply_blend` for the
    season being drafted. `adp` is `(player_id, position, adp)` (see this
    module's docstring for why it is a plain argument, not fetched here).
    `weekly_actuals`/`consistency_seasons`/`rules` feed
    `consistency.compute_consistency` for historical variance; `players` is
    passed through as the blended player pool so rookies with no weekly
    history still get a board row (with null consistency columns).

    Consistency is joined onto the blended frame *before* VOR is computed,
    because `risk_adjusted_vor` falls back to a `stddev` column when a
    player's `EP_p25`/`EP_p75` (quantile-blend) columns are null -- that
    fallback only works if `stddev` is already present when
    `risk_adjusted_vor` runs.

    Returns one row per player, sorted by `vor` descending, with `rank`
    assigned from that order (1 = best `VOR`).
    """
    blended = apply_blend(projections, weights_path, rules)

    consistency = compute_consistency(
        weekly_actuals,
        consistency_seasons,
        rules,
        players=blended.select(["player_id", "position"]),
        roster_config=roster_config,
        teams=teams,
    )
    with_consistency = blended.join(
        consistency, on=["player_id", "position"], how="left"
    )

    scored = compute_vor(with_consistency, adp, teams=teams)
    scored = risk_adjusted_vor(scored, lambda_risk=lambda_risk)
    scored = dropoff_value(scored, adp, picks_ahead=picks_ahead)
    scored = roster_math_replacement(scored, roster_config=roster_config, teams=teams)
    scored = positional_zscore(scored)

    board = scored.select(
        pl.col("player_name_raw").alias("player"),
        pl.col("position").alias("pos"),
        pl.col("team"),
        pl.col("EP").alias("ep"),
        pl.col("VOR").alias("vor"),
        pl.col("risk_adjusted_vor"),
        pl.col("dropoff_value"),
        pl.col("roster_math_vor"),
        pl.col("positional_zscore"),
        pl.col("stddev"),
        pl.col("floor"),
        pl.col("ceiling"),
        pl.col("pct_weeks_above_baseline"),
        pl.col("adp"),
    ).sort("vor", descending=True, nulls_last=True)

    board = board.with_columns(pl.int_range(1, board.height + 1).alias("rank"))

    return board.select(BOARD_COLUMNS)
