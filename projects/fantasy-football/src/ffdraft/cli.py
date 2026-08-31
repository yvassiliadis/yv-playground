"""CLI for fantasy football draft-prep pipeline."""

from pathlib import Path

import polars as pl
import typer

from ffdraft.ingest.actuals import load_dst_actuals, load_weekly_actuals
from ffdraft.ingest.archives import load_ffa_archives, load_ffdp_archives
from ffdraft.ingest.snapshot import run_snapshot

app = typer.Typer()
ingest_app = typer.Typer(help="Data ingestion commands.")
app.add_typer(ingest_app, name="ingest")

RAW_ACTUALS_DIR = Path("data/raw/actuals")
HISTORICAL_DIR = Path("data/historical")


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


if __name__ == "__main__":
    app()
