"""``tabula servers`` subcommand group — manage the pubkey pinning store.

The Typer subapp lives in :mod:`tabula_cli.servers.commands`; the root
``tabula_cli.main`` mounts it.

Pin-store persistence itself lives wire-side in
:mod:`tabula_wire.client.pinning`; this package owns only argument
parsing, output formatting, and exit codes.
"""

from tabula_cli.servers.commands import app

__all__ = ["app"]
