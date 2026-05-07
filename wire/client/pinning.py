"""Pinning store stub (#32).

Exposes the read-side API the chat UI needs: `lookup(label)`. The real
file-format reader/writer and `tabula servers` subcommands live in #32.
This stub returns ``None`` (label not pinned) for any input, which makes
the chat UI fall back to its `--host`/`--port`/`--accept-key` flags.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServerEntry:
    """Resolved pinning record for a known server label."""

    label: str
    host: str
    port: int
    pubkey: bytes


def lookup(label: str) -> ServerEntry | None:
    """Resolve a label to a `ServerEntry`, or ``None`` if not pinned.

    Stub: real implementation reads ``~/.config/tabula/known_servers``
    in #32.
    """

    return None


__all__ = ["ServerEntry", "lookup"]
