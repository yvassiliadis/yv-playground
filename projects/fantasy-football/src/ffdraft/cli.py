"""CLI for fantasy football draft-prep pipeline."""

import typer

app = typer.Typer()


@app.command()
def version() -> None:
    """Show version information."""
    typer.echo("ffdraft 0.1.0")


if __name__ == "__main__":
    app()
