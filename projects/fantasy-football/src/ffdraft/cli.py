"""CLI for fantasy football draft-prep pipeline."""

from pathlib import Path

import polars as pl
import typer

from ffdraft import scoring
from ffdraft.board import BOARD_COLUMNS, build_board
from ffdraft.config import TEAMS
from ffdraft.ingest.actuals import load_dst_actuals, load_weekly_actuals
from ffdraft.ingest.archives import load_ffa_archives, load_ffdp_archives
from ffdraft.ingest.snapshot import run_snapshot
from ffdraft.model import calibrate as calibrate_module

app = typer.Typer()
ingest_app = typer.Typer(help="Data ingestion commands.")
app.add_typer(ingest_app, name="ingest")
model_app = typer.Typer(help="Modelling commands.")
app.add_typer(model_app, name="model")

RAW_ACTUALS_DIR = Path("data/raw/actuals")
HISTORICAL_DIR = Path("data/historical")
SCORING_RULES_PATH = Path("data/scoring_rules.csv")


@app.command()
def version() -> None:
    """Show version information."""
    typer.echo("ffdraft 0.1.0")


def _parse_seasons(seasons: str) -> list[int]:
    """Parse a `START-END` range or comma-separated list of seasons."""
    if "-" in seasons:
        start, end = seasons.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(s) for s in seasons.split(",")]


@ingest_app.command("actuals")
def ingest_actuals(
    seasons: str = typer.Option(
        ...,
        "--seasons",
        help="Season range, e.g. 2008-2025, or comma-separated: 2023,2024",
    ),
) -> None:
    """Ingest actual player and DST stats via nflreadpy, writing partitioned Parquet.

    The raw layer is append-only: each season partition is a single
    `actuals.parquet` file, and re-running this command for a season reads
    whatever's already there and concatenates the newly-loaded rows onto it
    rather than overwriting. (A snapshot-qualified filename per run was the
    other option considered, but a single accumulating file per season
    keeps the partition layout simple for downstream readers -- one file to
    scan per season -- at the cost of the raw layer potentially containing
    duplicate rows across repeated runs, which downstream consumers should
    dedupe on `snapshot_date` if that matters for their use case.)
    """
    season_list = _parse_seasons(seasons)
    combined = pl.concat(
        [load_weekly_actuals(season_list), load_dst_actuals(season_list)],
        how="vertical",
    )

    for (season,), season_df in combined.group_by(["season"]):
        out_dir = RAW_ACTUALS_DIR / f"season={season}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "actuals.parquet"
        if out_path.exists():
            season_df = pl.concat(
                [pl.read_parquet(out_path), season_df], how="vertical"
            )
        season_df.write_parquet(out_path)

    typer.echo(f"Wrote actuals for seasons: {season_list}")


@ingest_app.command("snapshot")
def ingest_snapshot(
    season: int = typer.Option(
        ..., "--season", help="Season to fetch projections for."
    ),
    week: int = typer.Option(0, "--week", help="0 for seasonal projections."),
    sources: str | None = typer.Option(
        None,
        "--sources",
        help="Comma-separated source names to run, e.g. sleeper,espn. "
        "Defaults to every registered source.",
    ),
) -> None:
    """Fetch a projections snapshot from every registered source (or a subset).

    Tolerates one source failing -- it logs the failure and continues with
    the rest, then prints an "N/M sources succeeded" summary.
    """
    source_list = [s.strip() for s in sources.split(",")] if sources else None
    run_snapshot(season=season, week=week, sources=source_list)


@ingest_app.command("archives")
def ingest_archives(
    path: Path = typer.Option(
        ...,
        "--path",
        help="Directory containing 'ffa' and 'ffdp' subdirectories of "
        "already-downloaded archive files. Neither subdirectory needs to "
        "exist -- a missing one just yields zero rows for that loader.",
    ),
) -> None:
    """Parse local FFA and ffdp historical archive files into canonical
    Parquet, writing `data/historical/ffa.parquet` and
    `data/historical/ffdp.parquet`.

    No network access -- both loaders only read whatever files are already
    on disk under `path`. See `ffdraft.ingest.archives`'s module docstring
    for the (unverified against real downloads) archive formats assumed.
    """
    ffa = load_ffa_archives(path / "ffa")
    ffdp = load_ffdp_archives(path / "ffdp")

    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    ffa.write_parquet(HISTORICAL_DIR / "ffa.parquet")
    ffdp.write_parquet(HISTORICAL_DIR / "ffdp.parquet")

    typer.echo(
        f"Wrote {ffa.height} FFA rows and {ffdp.height} ffdp rows to {HISTORICAL_DIR}"
    )


@model_app.command("calibrate")
def model_calibrate(
    projections: Path = typer.Option(
        ...,
        "--projections",
        help="Parquet of canonical long-schema historical projections "
        "(multiple sources).",
    ),
    actuals: Path = typer.Option(
        ...,
        "--actuals",
        help="Parquet of canonical long-schema actuals (nflverse).",
    ),
    scoring_rules: Path = typer.Option(
        SCORING_RULES_PATH, "--scoring-rules", help="Scoring rules CSV."
    ),
    lambda_: float = typer.Option(
        calibrate_module.RECENCY_LAMBDA,
        "--lambda",
        help="Recency decay: each training row is weighted exp(-lambda * years_ago).",
    ),
    out: Path = typer.Option(
        calibrate_module.DEFAULT_SOURCE_WEIGHTS_PATH,
        "--out",
        help="Where to write the fitted per-position source weights.",
    ),
) -> None:
    """Run LOSO calibration across all six calibrators and write source weights.

    Prints the per-position x per-calibrator comparison table, then refits
    each position's winner on all seasons and writes the result to `--out`
    for `model/blend.py` to consume without re-fitting.
    """
    rules = scoring.load_scoring_rules(scoring_rules)
    loso_df, weights_df = calibrate_module.run_calibration(
        pl.read_parquet(projections),
        pl.read_parquet(actuals),
        rules,
        lambda_=lambda_,
        out_path=out,
    )
    calibrate_module.print_loso_table(loso_df)
    typer.echo(f"Wrote {weights_df.height} position weight rows to {out}")


@app.command()
def board(
    projections: Path = typer.Option(
        ...,
        "--projections",
        help="Parquet of canonical long-schema projections for the season "
        "being drafted (multiple sources).",
    ),
    adp: Path = typer.Option(
        ...,
        "--adp",
        help="Parquet or CSV of average-draft-position data: player_id, "
        "position, adp columns. No ingestion task builds this yet, so it "
        "must be sourced separately (e.g. a manually maintained file).",
    ),
    actuals: Path = typer.Option(
        ...,
        "--actuals",
        help="Parquet of canonical long-schema weekly actuals (single "
        "source) for the consistency metrics' historical window.",
    ),
    consistency_seasons: str = typer.Option(
        ...,
        "--consistency-seasons",
        help="Season range or comma-separated list to compute consistency "
        "over, e.g. 2023-2024 or 2023,2024.",
    ),
    weights: Path = typer.Option(
        calibrate_module.DEFAULT_SOURCE_WEIGHTS_PATH,
        "--weights",
        help="Fitted per-position source weights from `model calibrate`.",
    ),
    scoring_rules: Path = typer.Option(
        SCORING_RULES_PATH, "--scoring-rules", help="Scoring rules CSV."
    ),
    teams: int = typer.Option(TEAMS, "--teams", help="League size."),
    out: Path = typer.Option(..., "--out", help="Where to write the board CSV."),
) -> None:
    """Build the ranked draft board and write it to CSV, printing it too."""
    rules = scoring.load_scoring_rules(scoring_rules)
    board_df = build_board(
        projections=pl.read_parquet(projections),
        weights_path=weights,
        adp=pl.read_parquet(adp) if adp.suffix == ".parquet" else pl.read_csv(adp),
        weekly_actuals=pl.read_parquet(actuals),
        consistency_seasons=_parse_seasons(consistency_seasons),
        rules=rules,
        teams=teams,
    )

    board_df.write_csv(out)

    from rich.console import Console
    from rich.table import Table

    table = Table(title="Draft board")
    for column in BOARD_COLUMNS:
        table.add_column(column, justify="right" if column != "player" else "left")

    for row in board_df.iter_rows(named=True):
        table.add_row(*(_format_board_cell(row[column]) for column in BOARD_COLUMNS))

    Console().print(table)
    typer.echo(f"Wrote {board_df.height} board rows to {out}")


def _format_board_cell(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


if __name__ == "__main__":
    app()
