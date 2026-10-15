"""Eagleview command-line interface."""

from pathlib import Path
from typing import Annotated

import typer

from eagleview import __version__

app = typer.Typer(no_args_is_help=True, help="Eagleview: discover once, replay deterministically.")
schema_app = typer.Typer(no_args_is_help=True, help="Artifact schemas.")
app.add_typer(schema_app, name="schema")


@app.callback()
def main() -> None:
    """Eagleview command-line interface."""


@app.command()
def version() -> None:
    """Print the installed Eagleview version."""
    typer.echo(__version__)


@schema_app.command("export")
def schema_export(
    out: Annotated[Path, typer.Option(help="Directory for the *.schema.json files.")] = Path(
        "schemas"
    ),
) -> None:
    """Write JSON Schema for every artifact type, generated from the Pydantic models."""
    from eagleview.artifacts.json_schema import export_schemas

    for path in export_schemas(out):
        typer.echo(path)
