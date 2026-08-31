"""CLI for fantasy football draft-prep pipeline."""

from pathlib import Path

import polars as pl
import typer

from ffdraft.ingest.actuals import load_dst_actuals, load_weekly_actuals

app = typer.Typer()
ingest_app = typer.Typer(help="Data ingestion commands.")
app.add_typer(ingest_app, name="ingest")

RAW_ACTUALS_DIR = Path("data/raw/actuals")


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


if __name__ == "__main__":
    app()
