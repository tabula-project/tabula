"""`tabula chat` Typer subcommand group.

The Typer ``app`` is mounted on the canonical CLI entrypoint in
:mod:`tabula_cli.main` (owned by #57's canonical layout).
"""

from tabula_cli.chat.main import app

__all__ = ["app"]
