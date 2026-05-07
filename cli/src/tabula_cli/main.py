"""``tabula`` CLI top-level Typer app.

Currently registered subcommand groups:

- ``enclave`` -- provision and manage GCP-backed Tabula enclaves
- ``servers`` -- manage the client-side server pubkey pinning store (#32)
- ``keygen``  -- generate and inspect Tabula static X25519 keypairs (#46,
  relocated from ``tabula-wire`` per #57 / #68)
"""

from __future__ import annotations

import typer

from tabula_cli.enclave import app as enclave_app
from tabula_cli.keygen import app as keygen_app
from tabula_cli.servers.commands import app as servers_app

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

app.add_typer(
    servers_app,
    name="servers",
    help="Manage pinned server pubkeys (~/.config/tabula/known_servers).",
)

app.add_typer(
    keygen_app,
    name="keygen",
    help="Generate or inspect a Tabula X25519 static keypair.",
)


def main() -> None:
    """Console-script entry point. Wraps the Typer app."""
    app()


__all__ = ["app", "main"]
