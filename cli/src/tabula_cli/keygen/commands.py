"""``tabula keygen`` subcommand — Typer port.

Generates a long-lived X25519 static keypair for either the client or server
side of the Noise XX handshake. The secret is written to a permission-
restricted file (``0o600``); the public key is printed to stdout in a stable
hex format suitable for sharing or pinning.

See :mod:`tabula_wire.crypto.keys` for the canonical file format.

Surface (preserved verbatim from the pre-#68 argparse implementation):

- ``tabula keygen`` — generate (default).
- ``tabula keygen show <path>`` — read-only inspection of an existing file.

Flags on the generate (default) action:

- ``--out PATH`` (default: ``~/.config/tabula/{role}_key``)
- ``--role {client,server}`` (default: ``client``)
- ``--force`` (overwrite existing file; default refuses)
- ``--print-pub`` / ``--no-print-pub`` (default: print)

Originated in PR #46 under ``tabula_wire.cli.keygen``. Relocated here per
architect proposal #57 (CLI surface belongs in ``tabula-cli``; pure crypto
primitives stay in ``tabula-wire``); see #68 for the move follow-up.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Final, Optional

import typer

from tabula_wire.crypto.keys import (
    KeyFileError,
    generate_keypair,
    load_secret_key,
    save_secret_key,
)

ROLE_CLIENT: Final[str] = "client"
ROLE_SERVER: Final[str] = "server"
ROLES: Final[tuple[str, ...]] = (ROLE_CLIENT, ROLE_SERVER)


def default_key_path(role: str) -> Path:
    """Return the default secret-key path for *role*.

    Both client and server defaults live under the user's XDG config dir.
    System-service deployments of the server may prefer ``/etc/tabula/server_key``
    and pass ``--out`` explicitly; we don't try to auto-detect that here
    because guessing wrong would silently put the key in the wrong place.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")

    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        base = Path(config_home) / "tabula"
    else:
        base = Path.home() / ".config" / "tabula"

    return base / f"{role}_key"


# Typer subapp mounted on the canonical root in ``tabula_cli.main``.
#
# We use ``invoke_without_command=True`` on the callback so that
# ``tabula keygen`` (no subcommand) runs the generate flow, while
# ``tabula keygen show PATH`` routes to the explicit ``show`` command.
# The flags belong on the callback (not on a leaf command) for two
# reasons:
#
# 1. Backwards compatibility: pre-#68 ``tabula keygen --role server``
#    placed the flags before the (absent) subcommand. Putting them on
#    the callback preserves that surface.
# 2. ``tabula keygen --help`` must list ``--role``, ``--force``, ``--out``
#    in its synopsis (asserted by the ported tests). Typer renders
#    callback options in the parent group's help.
app = typer.Typer(
    name="keygen",
    help=(
        "Generate or inspect a Tabula X25519 static keypair. "
        "Generate a long-lived X25519 keypair for the Noise XX "
        "handshake. The secret is written to a 0600 file; the public "
        "key is printed in 64-char hex."
    ),
    no_args_is_help=False,
    add_completion=False,
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    role: str = typer.Option(
        ROLE_CLIENT,
        "--role",
        case_sensitive=False,
        help="Role this key serves (affects only the default --out path).",
    ),
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        help=(
            "Path to write the secret key. Defaults to "
            "~/.config/tabula/{role}_key."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing key file. Refuses by default.",
    ),
    print_pub: bool = typer.Option(
        True,
        "--print-pub/--no-print-pub",
        help="Print the public key to stdout (default: enabled).",
    ),
) -> None:
    """Default action (no subcommand): generate a fresh keypair.

    When the user invokes ``tabula keygen show PATH`` instead, Typer
    dispatches to :func:`_show_cmd` and this callback is a no-op for
    the generate-side flags.
    """
    # Validate --role at the callback so a bad value fails before we
    # branch on the subcommand. Typer's ``case_sensitive=False`` does
    # not constrain the choice set on a free-form ``str`` option, so
    # we enforce it manually to keep the error path identical to the
    # pre-#68 argparse ``choices=ROLES`` behavior.
    if role not in ROLES:
        typer.echo(
            f"error: invalid --role {role!r}; expected one of {list(ROLES)}",
            err=True,
        )
        raise typer.Exit(code=2)

    if ctx.invoked_subcommand is not None:
        # ``show`` will run next; leave the options-as-callback-args
        # alone — ``show`` takes its own path argument.
        return

    rc = _generate(role=role, out=out, force=force, print_pub=print_pub)
    raise typer.Exit(code=rc)


@app.command("show")
def _show_cmd(
    path: Path = typer.Argument(
        ...,
        help="Path to an existing secret-key file.",
    ),
    role: Optional[str] = typer.Option(
        None,
        "--role",
        case_sensitive=False,
        help="Role label for the printed line (default: 'tabula').",
    ),
) -> None:
    """Print the public key for an existing secret-key file.

    Read a Tabula secret-key file and print its derived public key.
    Does not modify the file.
    """
    if role is not None and role not in ROLES:
        typer.echo(
            f"error: invalid --role {role!r}; expected one of {list(ROLES)}",
            err=True,
        )
        raise typer.Exit(code=2)

    rc = _show(path=path, role=role)
    raise typer.Exit(code=rc)


# --------------------------------------------------------------------- #
# Pure-Python implementations (no Typer types in the signature) so      #
# they're trivially unit-testable and so the stdout/stderr surface is   #
# identical to the pre-#68 argparse version.                            #
# --------------------------------------------------------------------- #


def _generate(
    *,
    role: str,
    out: Optional[Path],
    force: bool,
    print_pub: bool,
) -> int:
    out_path: Path = out if out is not None else default_key_path(role)
    out_path = out_path.expanduser()

    # Permissive-parent warning. We only raise on world-writable (handled
    # in save_secret_key); for group-writable we emit a warning here.
    parent = out_path.parent
    if parent.exists():
        try:
            mode = stat.S_IMODE(parent.stat().st_mode)
        except OSError:
            mode = None
        if mode is not None and (mode & stat.S_IWGRP):
            print(
                f"warning: parent directory {parent} is group-writable "
                f"(mode={oct(mode)}); consider chmod 700",
                file=sys.stderr,
            )

    try:
        secret = generate_keypair()
        written = save_secret_key(secret, out_path, overwrite=force)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyFileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"wrote secret key to {written} (mode 0o600)", file=sys.stderr)

    if print_pub:
        pub_hex = secret.public_key().hex()
        print(f"tabula {role} public key: {pub_hex}")

    return 0


def _show(*, path: Path, role: Optional[str]) -> int:
    try:
        secret = load_secret_key(path)
    except KeyFileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    pub_hex = secret.public_key().hex()
    if role is not None:
        print(f"tabula {role} public key: {pub_hex}")
    else:
        print(f"tabula public key: {pub_hex}")
    return 0
