"""Weekly consistency metrics: stddev, floor, ceiling, and a baseline-beat rate.

`compute_consistency` scores 1-2 seasons of weekly actuals through
`ffdraft.scoring.score()` and, per player, computes:

* `stddev`    -- sample stddev (`ddof=1`) of weekly fantasy points.
* `floor`     -- p25 weekly score.
* `ceiling`   -- p75 weekly score (the spec offers p75 or p90; p75 is used
                 here so `ceiling` sits on the same quartile grid as `floor`
                 -- both come from a `quantile()` call at a matching
                 percentile rather than mixing quartiles and deciles).
                 Both use `interpolation="linear"` explicitly: Polars'
                 `quantile()` defaults to `"nearest"`, which disagrees with
                 `numpy.percentile`'s (and most readers') default "linear"
                 interpolation for non-integer ranks -- e.g. for `[1,2,3,4]`,
                 "nearest" gives p25/p75 of `2.0`/`3.0` while "linear" gives
                 `1.75`/`3.25`. "linear" is used here so `floor`/`ceiling`
                 match the intuitive "p25/p75 week" reading and
                 `numpy.percentile`, which is what a caller would reach for
                 to sanity-check this by hand.
* `pct_weeks_above_baseline` -- fraction of the player's weeks scoring above
  that week's positional-starter baseline: the median score, within that
  same week and position, among the top `starters x teams` scorers (from
  `config.py`'s roster config) -- so the baseline moves with bye weeks and
  injuries instead of being one static number for a whole season.

A player with fewer than `MIN_QUALIFYING_WEEKS` scored weeks gets `None` for
all four columns rather than a statistic computed on noise, and is not
dropped from the output -- see `MIN_QUALIFYING_WEEKS`'s docstring. Passing
`players` (the full draft-pool player list, including rookies with zero
weekly rows at all) guarantees every one of those players gets an output row
even though they never appear in `weekly_actuals` to begin with; the `None`
returned is a typed null, not the string em dash a table renderer might
display in its place -- that formatting choice belongs to whatever renders
the table, not to this function.
"""

from __future__ import annotations

import logging

import polars as pl

from ffdraft import scoring
from ffdraft.config import DEFAULT_ROSTER_CONFIG, TEAMS, RosterConfig

logger = logging.getLogger(__name__)

#: Below this many scored weeks, stddev/floor/ceiling/pct-above-baseline are
#: statistically meaningless (a two-week stddev is noise dressed up as a
#: statistic) -- such rows get None for all four columns instead. 3 is
#: deliberately low: it is meant to gate out "played one snap before an
#: injury", not to demand a full season of history.
MIN_QUALIFYING_WEEKS = 3

CONSISTENCY_COLUMNS = [
    "player_id",
    "position",
    "stddev",
    "floor",
    "ceiling",
    "pct_weeks_above_baseline",
]

_QUALIFYING_COLUMNS = ["stddev", "floor", "ceiling", "pct_weeks_above_baseline"]


def _weekly_baseline(
    scored: pl.DataFrame, roster_config: RosterConfig, teams: int
) -> pl.DataFrame:
    """Median score of the top `starters * teams` players, per season/week/position."""
    positions = [p for p in scored["position"].unique().to_list() if p is not None]
    unconfigured = [p for p in positions if roster_config.starters_at(p) == 0]
    if unconfigured:
        logger.warning(
            "compute_consistency: position(s) %s have no starters entry in "
            "roster_config -- their baseline falls back to the single top "
            "scorer that week (N clipped to 1), not a real starters*teams count",
            sorted(unconfigured),
        )
    starter_counts = {
        position: teams * roster_config.starters_at(position) for position in positions
    }
    ranked = scored.sort(
        ["season", "week", "position", "fantasy_points"],
        descending=[False, False, False, True],
    ).with_columns(
        pl.int_range(1, pl.len() + 1)
        .over(["season", "week", "position"])
        .alias("_rank"),
        pl.col("position")
        .replace_strict(starter_counts, default=0, return_dtype=pl.Int64)
        .clip(lower_bound=1)
        .alias("_n_starters"),
    )
    top = ranked.filter(pl.col("_rank") <= pl.col("_n_starters"))
    return top.group_by(["season", "week", "position"]).agg(
        pl.col("fantasy_points").median().alias("_baseline")
    )


def compute_consistency(
    weekly_actuals: pl.DataFrame,
    seasons: list[int],
    rules: dict[str, float],
    players: pl.DataFrame | None = None,
    roster_config: RosterConfig = DEFAULT_ROSTER_CONFIG,
    teams: int = TEAMS,
) -> pl.DataFrame:
    """Per-player weekly consistency over `seasons` of `weekly_actuals`.

    `weekly_actuals` is the canonical long schema; `rules` is the same
    scoring-rules dict `scoring.score()` always takes. `weekly_actuals` must
    carry a single `source` (mirrors `calibrate.build_training_frame`: two
    sources for the same player-week would double-count fantasy points).
    `players`, if given, is a `(player_id, position)` frame of the full
    player universe -- pass this to guarantee rookies with zero weekly rows
    still get an output row (with `None` metrics) instead of being absent
    because they were never in `weekly_actuals` to begin with.
    """
    filtered = weekly_actuals.filter(pl.col("season").is_in(seasons))

    sources = filtered["source"].unique().sort().to_list()
    if len(sources) > 1:
        raise ValueError(
            "compute_consistency: weekly_actuals must carry exactly one source "
            f"(summing would double-count weekly points), got {sources}"
        )

    position_by_player = (
        filtered.select(["player_id", "position"])
        .drop_nulls()
        .group_by("player_id")
        .agg(pl.col("position").first())
    )

    scored = scoring.score(filtered, rules).join(
        position_by_player, on="player_id", how="left"
    )

    baseline = _weekly_baseline(scored, roster_config, teams)
    with_baseline = scored.join(baseline, on=["season", "week", "position"], how="left")

    per_player = with_baseline.group_by(["player_id", "position"]).agg(
        pl.len().alias("_n_weeks"),
        pl.col("fantasy_points").std().alias("stddev"),
        pl.col("fantasy_points").quantile(0.25, interpolation="linear").alias("floor"),
        pl.col("fantasy_points")
        .quantile(0.75, interpolation="linear")
        .alias("ceiling"),
        (pl.col("fantasy_points") > pl.col("_baseline"))
        .mean()
        .alias("pct_weeks_above_baseline"),
    )

    per_player = per_player.with_columns(
        [
            pl.when(pl.col("_n_weeks") >= MIN_QUALIFYING_WEEKS)
            .then(pl.col(c))
            .otherwise(None)
            .alias(c)
            for c in _QUALIFYING_COLUMNS
        ]
    ).drop("_n_weeks")

    if players is not None:
        result = players.select(["player_id", "position"]).join(
            per_player, on=["player_id", "position"], how="left"
        )
    else:
        result = per_player

    return result.select(CONSISTENCY_COLUMNS).sort(["position", "player_id"])
