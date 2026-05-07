"""``tabula keygen`` subcommand group.

Surface (preserved from #46, originally shipped under ``tabula_wire.cli``;
moved here per architect proposal #57 / follow-up #68):

    tabula keygen [--out PATH] [--role {client,server}] [--force]
                  [--print-pub | --no-print-pub]
    tabula keygen show <path> [--role {client,server}]

The ``app`` Typer instance is mounted onto the canonical root in
:mod:`tabula_cli.main` via ``app.add_typer(keygen.app, name="keygen")``.

The crypto primitives backing keygen live in
:mod:`tabula_wire.crypto.keys` (still part of the ``tabula-wire``
distribution); ``tabula-cli`` consumes them via its declared dependency
on ``tabula-wire``.
"""

from tabula_cli.keygen.commands import app, default_key_path

__all__ = ["app", "default_key_path"]
