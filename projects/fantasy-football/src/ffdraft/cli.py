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
    """Ingest actual player and DST stats via nflreadpy, writing partitioned Parquet."""
    season_list = _parse_seasons(seasons)
    combined = pl.concat(
        [load_weekly_actuals(season_list), load_dst_actuals(season_list)],
        how="vertical",
    )

    for (season,), season_df in combined.group_by(["season"]):
        out_dir = RAW_ACTUALS_DIR / f"season={season}"
        out_dir.mkdir(parents=True, exist_ok=True)
        season_df.write_parquet(out_dir / "actuals.parquet")

    typer.echo(f"Wrote actuals for seasons: {season_list}")


if __name__ == "__main__":
    app()
