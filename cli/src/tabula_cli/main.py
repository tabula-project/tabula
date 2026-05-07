"""``tabula`` CLI top-level Typer app.

Currently registered subcommand groups:

- ``enclave`` -- provision and manage GCP-backed Tabula enclaves
"""

from __future__ import annotations

import typer

from tabula_cli.enclave import app as enclave_app

app = typer.Typer(
    name="tabula",
    help="Tabula: sovereign-AI substrate operator CLI.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(
    enclave_app,
    name="enclave",
    help="Provision and manage Tabula enclaves on GCP.",
)


def main() -> None:
    """Console-script entry point. Wraps the Typer app."""
    app()


__all__ = ["app", "main"]
