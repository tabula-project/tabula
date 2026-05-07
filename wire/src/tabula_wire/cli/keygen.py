"""``tabula keygen`` subcommand.

Generates a long-lived X25519 static keypair for either the client or server
side of the Noise XX handshake. The secret is written to a permission-
restricted file (``0o600``); the public key is printed to stdout in a stable
hex format suitable for sharing or pinning.

See ``tabula_wire.crypto.keys`` for the canonical file format.

Acceptance criteria from issue #31:

- ``tabula keygen`` subcommand with ``--out``, ``--role``, ``--force``,
  ``--print-pub``.
- Output file format: ``tabula-x25519-secret-v1`` magic + lowercase hex.
- Secret-key file written with mode ``0o600``; warn on permissive parent.
- Public key printed to stdout as 64-char hex with a labeled line.
- Companion ``tabula keygen show <path>`` reads an existing file and prints
  its public key without regenerating.

Out of scope (per #31): rotation, multi-key, HSM, passphrase encryption,
pinning store.

# TODO(#future): passphrase-encrypted key files (would introduce a v2 magic
# header in tabula_wire.crypto.keys).
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path
from typing import Final

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


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Attach the ``keygen`` subcommand to *subparsers*."""
    p = subparsers.add_parser(
        "keygen",
        help="Generate or inspect a Tabula X25519 static keypair.",
        description=(
            "Generate a long-lived X25519 keypair for the Noise XX handshake. "
            "The secret is written to a 0600 file; the public key is printed "
            "in 64-char hex."
        ),
    )

    keygen_sub = p.add_subparsers(dest="keygen_cmd")
    # No `required=True` because the default (no subcommand) means "generate".

    # --- generate (default) ---
    p.add_argument(
        "--role",
        choices=ROLES,
        default=ROLE_CLIENT,
        help="Role this key serves (affects only the default --out path).",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Path to write the secret key. Defaults to "
            "~/.config/tabula/{role}_key."
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing key file. Refuses by default.",
    )
    pub_grp = p.add_mutually_exclusive_group()
    pub_grp.add_argument(
        "--print-pub",
        dest="print_pub",
        action="store_true",
        default=True,
        help="Print the public key to stdout (default).",
    )
    pub_grp.add_argument(
        "--no-print-pub",
        dest="print_pub",
        action="store_false",
        help="Suppress public-key output.",
    )

    # --- show <path> (read-only inspection) ---
    show = keygen_sub.add_parser(
        "show",
        help="Print the public key for an existing secret-key file.",
        description=(
            "Read a Tabula secret-key file and print its derived public key. "
            "Does not modify the file."
        ),
    )
    show.add_argument(
        "path",
        type=Path,
        help="Path to an existing secret-key file.",
    )
    show.add_argument(
        "--role",
        choices=ROLES,
        default=None,
        help="Role label for the printed line (default: 'tabula').",
    )

    p.set_defaults(_handler=_dispatch)


def _dispatch(args: argparse.Namespace) -> int:
    """Route to ``generate`` or ``show`` based on the parsed namespace."""
    if getattr(args, "keygen_cmd", None) == "show":
        return _show(args)
    return _generate(args)


def _generate(args: argparse.Namespace) -> int:
    role: str = args.role
    out_path: Path = args.out if args.out is not None else default_key_path(role)
    out_path = out_path.expanduser()

    # Permissive-parent warning. We only raise on world-writable (handled in
    # save_secret_key); for group-writable we emit a warning here.
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
        written = save_secret_key(secret, out_path, overwrite=args.force)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyFileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"wrote secret key to {written} (mode 0o600)", file=sys.stderr)

    if args.print_pub:
        pub_hex = secret.public_key().hex()
        print(f"tabula {role} public key: {pub_hex}")

    return 0


def _show(args: argparse.Namespace) -> int:
    try:
        secret = load_secret_key(args.path)
    except KeyFileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    pub_hex = secret.public_key().hex()
    if args.role is not None:
        print(f"tabula {args.role} public key: {pub_hex}")
    else:
        print(f"tabula public key: {pub_hex}")
    return 0
