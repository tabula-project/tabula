"""``tabula servers`` subcommand group — manage the pubkey pinning store.

Surface (per #32):

    tabula servers add <label> <host>:<port> <hex-pubkey> [--force]
    tabula servers list
    tabula servers remove <label>

All persistence is delegated to :mod:`tabula_wire.client.pinning`. This
module owns only Typer command wiring, output formatting, and exit codes.

The ``app`` Typer instance is mounted onto the canonical root in
:mod:`tabula_cli.main` via ``app.add_typer(servers_app, name="servers")``.
"""

from __future__ import annotations

import sys

import typer

from tabula_wire.client import pinning
from tabula_wire.client.exceptions import (
    DuplicateLabelError,
    InvalidPubkeyError,
    UnknownLabelError,
)

app = typer.Typer(
    name="servers",
    help=(
        "Manage the client-side server pubkey pinning store. Tabula "
        "refuses to silently TOFU on first connect; obtain pubkeys "
        "out-of-band and pin them here before using `tabula chat connect`."
    ),
    no_args_is_help=True,
    add_completion=False,
)


# ---------- helpers -----------------------------------------------------------


def _err(msg: str) -> None:
    """Write a one-line error to stderr (no traceback)."""
    print(f"error: {msg}", file=sys.stderr)


def _parse_endpoint(endpoint: str) -> tuple[str, int]:
    """Parse ``host:port`` for the CLI; raises ``ValueError`` on bad input."""
    if ":" not in endpoint:
        raise ValueError(f"expected host:port, got {endpoint!r}")
    host, _, port_s = endpoint.rpartition(":")
    if not host:
        raise ValueError(f"empty host in {endpoint!r}")
    try:
        port = int(port_s)
    except ValueError as e:
        raise ValueError(f"non-integer port in {endpoint!r}") from e
    if not (1 <= port <= 65535):
        raise ValueError(f"port out of range in {endpoint!r}: {port}")
    return host, port


def _parse_pubkey(hex_str: str) -> bytes:
    """Parse a 64-char lowercase hex pubkey to 32 bytes; raises on bad input."""
    s = hex_str.strip()
    if len(s) != 64:
        raise InvalidPubkeyError(
            f"pubkey must be 64 lowercase hex chars (got {len(s)})"
        )
    if any(c not in "0123456789abcdef" for c in s):
        raise InvalidPubkeyError(
            "pubkey must be lowercase hex (0-9, a-f); use `tabula keygen` to generate"
        )
    return bytes.fromhex(s)


# ---------- commands ----------------------------------------------------------


@app.command("add")
def add_cmd(
    label: str = typer.Argument(..., help="Local nickname for this server."),
    endpoint: str = typer.Argument(
        ...,
        metavar="HOST:PORT",
        help="TCP endpoint where the server listens.",
    ),
    pubkey: str = typer.Argument(
        ...,
        metavar="HEX_PUBKEY",
        help="Server's static X25519 public key as 64 lowercase hex chars.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing entry with the same label.",
    ),
) -> None:
    """Pin a server pubkey for a label."""
    try:
        host, port = _parse_endpoint(endpoint)
    except ValueError as e:
        _err(str(e))
        raise typer.Exit(code=2)

    try:
        key = _parse_pubkey(pubkey)
    except InvalidPubkeyError as e:
        _err(str(e))
        raise typer.Exit(code=2)

    try:
        entry = pinning.add(label, host, port, key, force=force)
    except DuplicateLabelError as e:
        _err(str(e))
        raise typer.Exit(code=1)
    except (ValueError, InvalidPubkeyError) as e:
        _err(str(e))
        raise typer.Exit(code=2)

    print(
        f"pinned {entry.label} -> {entry.host}:{entry.port} "
        f"({entry.pubkey_hex})"
    )


@app.command("list")
def list_cmd() -> None:
    """List all pinned servers in a table."""
    entries = pinning.list_all()
    if not entries:
        print("(no pinned servers)")
        print(f"store: {pinning.store_path()}")
        return

    label_w = max(len("LABEL"), max(len(e.label) for e in entries))
    endpoint_w = max(
        len("ENDPOINT"),
        max(len(f"{e.host}:{e.port}") for e in entries),
    )
    header = f"{'LABEL':<{label_w}}  {'ENDPOINT':<{endpoint_w}}  PUBKEY"
    print(header)
    print("-" * len(header))
    for e in entries:
        endpoint = f"{e.host}:{e.port}"
        print(f"{e.label:<{label_w}}  {endpoint:<{endpoint_w}}  {e.pubkey_hex}")


@app.command("remove")
def remove_cmd(
    label: str = typer.Argument(..., help="Label to remove."),
) -> None:
    """Remove the pinned entry for a label."""
    try:
        entry = pinning.remove(label)
    except UnknownLabelError:
        _err(f"unknown server label {label!r}")
        raise typer.Exit(code=1)
    print(f"removed {entry.label} -> {entry.host}:{entry.port}")
