"""Value-over-replacement (VOR) and related draft-value columns.

`compute_vor` is the primary column: for each position, replacement level is
the blended `EP` of the `N_pos`-th-ranked player at that position, where
`N_pos` comes from how often that position is drafted in the first `teams *
ROUNDS_PER_TEAM` picks by ADP (`ROUNDS_PER_TEAM = 10`, so the default
`teams=10` reproduces the spec's literal "top 100 ADP picks"; scaling by
`teams` keeps the same ~10-round window at other league sizes). K/DST are
exempt from that derivation -- the spec fixes their replacement rank at
`K_DST_REPLACEMENT_N = 5` because so few are taken in the first few rounds
that an ADP-derived count would be noise.

The remaining four functions are independently callable (per the plan,
`board.py` calls them separately rather than through one monolithic
pipeline):

* `risk_adjusted_vor` -- `VOR` penalised by a `sigma(EP)` risk term.
* `dropoff_value` -- points lost by waiting for this position at your next
  pick, ADP-implied.
* `roster_math_replacement` -- a second, non-ADP replacement level derived
  from `config.py`'s starters x teams, for sanity-checking `compute_vor`.
* `positional_zscore` -- `EP` expressed in within-position standard
  deviations, another sanity-check column.
* `calibrate_lambda_risk` -- fits `lambda_risk` from history; not wired into
  `compute_vor`'s default path (see its docstring for why).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import polars as pl
from scipy.stats import spearmanr

from ffdraft.config import DEFAULT_ROSTER_CONFIG, TEAMS, RosterConfig

ROUNDS_PER_TEAM = 10
K_DST_REPLACEMENT_N = 5
K_DST_POSITIONS = frozenset({"K", "DST"})

#: `risk_adjusted_vor`'s default. The spec calls for calibrating this on
#: history (see `calibrate_lambda_risk`), but wiring a full historical
#: backtest into the default path is out of scope here -- 1.0 is a sane,
#: middle-of-the-road value (a one-point-of-VOR penalty per point of sigma),
#: not a fitted one.
DEFAULT_LAMBDA_RISK = 1.0

#: `(p75 - p25)` -> sigma conversion, assuming an approximately normal
#: weekly-points distribution: the interquartile range of a normal is
#: `1.34898 * sigma`.
_IQR_TO_SIGMA = 1.34898


def _ranked_by_ep(df: pl.DataFrame) -> pl.DataFrame:
    """`df` with a `_rank` column: 1 = highest `EP`, within each `position`."""
    return df.sort(["position", "EP"], descending=[False, True]).with_columns(
        pl.int_range(1, pl.len() + 1).over("position").alias("_rank")
    )


def _replacement_ep_by_position(
    ranked: pl.DataFrame, n_by_position: dict[str, int]
) -> pl.DataFrame:
    """`EP` at rank `n` per position, clamped to the available player count.

    A position with fewer than `n` ranked players uses its lowest-ranked
    (worst) player as replacement rather than producing a null -- there is no
    real "not enough players" case in a full player pool, only positions
    (K/DST early in an archive) that are thin.
    """
    records = []
    for position in ranked["position"].unique().to_list():
        n = n_by_position.get(position, 0)
        subset = ranked.filter(pl.col("position") == position)
        if subset.height == 0 or n <= 0:
            continue
        clamped_rank = min(n, subset.height)
        ep = subset.filter(pl.col("_rank") == clamped_rank)["EP"][0]
        records.append({"position": position, "_ep_replacement": ep})
    if not records:
        return pl.DataFrame(
            schema={"position": pl.String, "_ep_replacement": pl.Float64}
        )
    return pl.DataFrame(records)


def compute_vor(
    blended: pl.DataFrame, adp: pl.DataFrame, teams: int = TEAMS
) -> pl.DataFrame:
    """Add `VOR = EP - EP_replacement` to `blended`.

    `blended` needs `player_id`, `position`, `EP` (e.g. `blend.apply_blend`'s
    output). `adp` needs `player_id`, `position`, `adp` (lower = drafted
    earlier) and is used only to size `N_pos` -- ranking for the replacement
    player itself is always by `EP`, not by ADP.
    """
    pool_size = teams * ROUNDS_PER_TEAM
    top_pool = adp.sort("adp").head(pool_size)
    adp_counts = dict(
        top_pool.group_by("position").agg(pl.len().alias("n")).iter_rows()
    )

    ranked = _ranked_by_ep(blended)
    n_by_position = {
        position: (
            K_DST_REPLACEMENT_N
            if position in K_DST_POSITIONS
            else adp_counts.get(position, 0)
        )
        for position in ranked["position"].unique().to_list()
    }
    replacement = _replacement_ep_by_position(ranked, n_by_position)

    return (
        ranked.join(replacement, on="position", how="left")
        .with_columns((pl.col("EP") - pl.col("_ep_replacement")).alias("VOR"))
        .drop(["_rank", "_ep_replacement"])
    )


def risk_adjusted_vor(
    df: pl.DataFrame, lambda_risk: float = DEFAULT_LAMBDA_RISK
) -> pl.DataFrame:
    """Add `risk_adjusted_vor = VOR - lambda_risk * sigma(EP)`.

    `sigma(EP)` is read per row, in priority order:

    1. From `EP_p25`/`EP_p75` (the `QuantileBlend` floor/ceiling), converting
       the interquartile range to a normal-approximation sigma.
    2. From a `stddev` column (historical weekly variance, e.g. from
       `metrics.consistency.compute_consistency`).

    The two sources are combined per row, not per frame: a player whose
    quantile columns are null -- e.g. a `QuantileBlend` winner that abstained
    for that player and `blend.apply_blend` filled `EP` from its
    `EqualWeightMean` fallback, which has no quantile output of its own --
    falls through to `stddev` for THAT row even if other rows in the same
    frame have real quantile data. A row with neither ends up with a null
    `sigma_ep` and therefore a null `risk_adjusted_vor`, on purpose: treating
    an unknown variance as `0.0` would rank that player as risk-free and
    place them ABOVE otherwise-identical players with real variance data --
    exactly the failure mode this function exists to avoid. A null
    `risk_adjusted_vor` is a visible "insufficient data" signal a caller can
    filter or fall back on; a fabricated `0.0` is not.

    Raises `ValueError` if `df` has neither column at all -- there is no
    sigma estimate anywhere to fall back to, and the caller most likely just
    forgot to join a variance source in.
    """
    has_quantile = "EP_p25" in df.columns and "EP_p75" in df.columns
    has_stddev = "stddev" in df.columns
    if not has_quantile and not has_stddev:
        raise ValueError(
            "risk_adjusted_vor: df needs EP_p25/EP_p75 (QuantileBlend output) "
            "or a stddev column (metrics.consistency.compute_consistency output) "
            "to estimate sigma(EP)"
        )

    if has_quantile:
        sigma_expr = (pl.col("EP_p75") - pl.col("EP_p25")) / _IQR_TO_SIGMA
        if has_stddev:
            sigma_expr = sigma_expr.fill_null(pl.col("stddev"))
    else:
        sigma_expr = pl.col("stddev")

    return (
        df.with_columns(sigma_expr.alias("_sigma_ep"))
        .with_columns(
            (pl.col("VOR") - lambda_risk * pl.col("_sigma_ep")).alias(
                "risk_adjusted_vor"
            )
        )
        .drop("_sigma_ep")
    )


def dropoff_value(
    df: pl.DataFrame, adp: pl.DataFrame, picks_ahead: int = 10
) -> pl.DataFrame:
    """Add `dropoff_value`: points lost by waiting `picks_ahead` overall picks.

    `picks_ahead` counts overall draft picks (ADP units), not same-position
    rank slots -- a receiver-heavy stretch of the board means "10 picks
    later" can span very few other receivers, so this thresholds on
    `adp >= own_adp + picks_ahead` rather than skipping a fixed number of
    same-position rows. For each player, the comparator is the highest-`EP`
    same-position player at or after that ADP threshold: the best a team
    could still expect to get at that position if it passed now and picked
    again `picks_ahead` picks later. `dropoff_value = EP - comparator EP`;
    null for a player with no same-position player left on the board at or
    after the threshold.
    """
    joined = df.join(adp.select(["player_id", "adp"]), on="player_id", how="left")
    by_adp = joined.sort(["position", "adp"])
    # Suffix max of EP within each position, in ADP order: `_suffix_max[i]`
    # is the best EP among this player and everyone drafted later.
    by_adp = by_adp.with_columns(
        pl.col("EP").reverse().cum_max().reverse().over("position").alias("_suffix_max")
    )

    # For each player, find the first (lowest-adp) same-position row at or
    # past `adp + picks_ahead` via an as-of join on ADP value, then read off
    # its precomputed suffix max -- the best EP from that point onward.
    lookup = by_adp.select(["position", "adp", "_suffix_max"]).sort(["position", "adp"])
    probes = (
        joined.select(["player_id", "position", "adp"])
        .with_columns((pl.col("adp") + picks_ahead).alias("_threshold"))
        .sort(["position", "_threshold"])
    )
    matched = probes.join_asof(
        lookup,
        left_on="_threshold",
        right_on="adp",
        by="position",
        strategy="forward",
        check_sortedness=False,  # both sides are explicitly sorted above
    ).select(["player_id", pl.col("_suffix_max").alias("_comparator_ep")])

    return (
        joined.join(matched, on="player_id", how="left")
        .with_columns((pl.col("EP") - pl.col("_comparator_ep")).alias("dropoff_value"))
        .drop("_comparator_ep")
    )


def roster_math_replacement(
    df: pl.DataFrame,
    roster_config: RosterConfig = DEFAULT_ROSTER_CONFIG,
    teams: int = TEAMS,
) -> pl.DataFrame:
    """Add a second, roster-math replacement level and `roster_math_vor`.

    `N_pos = teams * roster_config.starters_at(position)` -- a pure
    starters-only count, deliberately not folding `FLEX` into RB/WR/TE (that
    would require assuming how a FLEX slot splits across positions, which is
    a modelling choice this sanity-check column intentionally avoids). Bench
    depth is not part of `N_pos` here either: `roster_config.bench` is
    position-agnostic in this league's config, so it cannot be attributed to
    any one position's replacement count.
    """
    ranked = _ranked_by_ep(df)
    n_by_position = {
        position: teams * roster_config.starters_at(position)
        for position in ranked["position"].unique().to_list()
    }
    replacement = _replacement_ep_by_position(ranked, n_by_position).rename(
        {"_ep_replacement": "roster_replacement_ep"}
    )
    return (
        ranked.join(replacement, on="position", how="left")
        .with_columns(
            (pl.col("EP") - pl.col("roster_replacement_ep")).alias("roster_math_vor")
        )
        .drop("_rank")
    )


def positional_zscore(df: pl.DataFrame) -> pl.DataFrame:
    """Add `positional_zscore`: `EP` in within-position standard deviations."""
    return df.with_columns(
        (
            (pl.col("EP") - pl.col("EP").mean().over("position"))
            / pl.col("EP").std().over("position")
        ).alias("positional_zscore")
    )


def calibrate_lambda_risk(
    historical_df: pl.DataFrame,
    candidate_lambdas: Sequence[float] = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0),
) -> float:
    """Pick the `lambda_risk` that would have ranked players best, ex post.

    `historical_df` needs `VOR`, `actual_points`, and either `EP_p25`/`EP_p75`
    or `stddev` (whatever `risk_adjusted_vor` needs), for past seasons. For
    each candidate, this scores `risk_adjusted_vor` against `actual_points`
    by Spearman rank correlation and returns the candidate with the highest
    correlation.

    This is a simplified proxy for "which lambda would have drafted best": a
    true backtest would replay a mock draft under each lambda and compare
    final-roster outcomes, which needs draft-simulation machinery this
    project does not have yet. Rank correlation against the season's actual
    points is a reasonable stand-in -- a lambda that produces rankings closer
    to how the season actually played out is, all else equal, a better
    risk/reward trade-off -- but it is not the full backtest the spec
    describes. `compute_vor`'s default path does not call this function; see
    `DEFAULT_LAMBDA_RISK`.
    """
    best_lambda = DEFAULT_LAMBDA_RISK
    best_score = -np.inf
    for lambda_risk in candidate_lambdas:
        adjusted = risk_adjusted_vor(historical_df, lambda_risk=lambda_risk)
        pair = adjusted.select(["risk_adjusted_vor", "actual_points"]).drop_nulls()
        if pair.height < 3:
            continue
        rho = spearmanr(
            pair["risk_adjusted_vor"].to_numpy(), pair["actual_points"].to_numpy()
        ).statistic
        if np.isnan(rho):
            continue
        if rho > best_score:
            best_score = rho
            best_lambda = float(lambda_risk)
    return best_lambda
