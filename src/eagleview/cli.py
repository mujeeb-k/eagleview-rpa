"""Eagleview command-line interface."""

import typer

from eagleview import __version__

app = typer.Typer(no_args_is_help=True, help="Eagleview: discover once, replay deterministically.")


@app.callback()
def main() -> None:
    """Eagleview command-line interface."""


@app.command()
def version() -> None:
    """Print the installed Eagleview version."""
    typer.echo(__version__)
