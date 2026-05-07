"""Client-side server pubkey pinning store.

Pins the server's static X25519 public key to a human-readable label, in a
flat text file at ``~/.config/tabula/known_servers`` (override via
``TABULA_CONFIG_DIR``). Format is line-oriented and SSH-``known_hosts`` in
spirit — one entry per line:

    <server-label> <host>:<port> <hex-pubkey>

Empty lines and ``#``-prefixed comments are allowed. The writer prepends a
short documentation header when it creates the file.

TOFU policy (locked by curator decision on #32): when ``tabula chat connect
<label>`` is invoked and ``label`` is *not* in the store, the chat client
refuses with a clear error suggesting ``tabula servers add``. This module
provides :func:`refuse_unknown_label_message` for the chat path to use; it
does not prompt-on-first-connect.

This module owns *only* the pinning store. The dialer (#27) consumes
:func:`lookup` to resolve a label, then performs the mismatch check itself
and raises :class:`tabula_wire.client.exceptions.ServerKeyMismatch` (the
``ClientError`` subclass — see :mod:`tabula_wire.client.exceptions`). The
error message templating helper :func:`mismatch_error_message` lives here
so the text is single-sourced.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tabula_wire.client.exceptions import (
    DuplicateLabelError,
    InvalidPubkeyError,
    UnknownLabelError,
)

__all__ = [
    "DEFAULT_STORE_HEADER",
    "ServerEntry",
    "add",
    "config_dir",
    "list_all",
    "lookup",
    "mismatch_error_message",
    "refuse_unknown_label_message",
    "remove",
    "store_path",
]

log = logging.getLogger(__name__)

# Filename inside the config dir.
_STORE_FILENAME = "known_servers"

# Documentation header prepended on first write so a human inspecting the
# file knows what they're looking at. Format: each non-blank, non-comment
# line is one pinned server: "<label> <host>:<port> <hex-pubkey>".
DEFAULT_STORE_HEADER = (
    "# tabula known_servers — client-side server pubkey pinning store\n"
    "# Format (one entry per line): <label> <host>:<port> <hex-pubkey>\n"
    "# - <label>: local nickname for the server (no whitespace)\n"
    "# - <host>:<port>: TCP endpoint (host may be a hostname or IP)\n"
    "# - <hex-pubkey>: 64 lowercase hex chars = 32-byte X25519 static pubkey\n"
    "# Lines starting with '#' and blank lines are ignored.\n"
    "# Managed by `tabula servers {add,list,remove}`. Edit at your own risk.\n"
)

# Permission bits. Pin store contains pubkeys (not secret) but matches the
# spec; the parent dir gets 0700 to keep tabula config private by convention.
_FILE_MODE = 0o600
_DIR_MODE = 0o700

# A server label must be a single non-whitespace token without ':' (which
# would collide with the host:port column) and without '#' (comment marker).
_LABEL_RE = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._-]*$")

# 64 lowercase hex chars = 32-byte X25519 pubkey.
_HEX_PUBKEY_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ServerEntry:
    """One pinned server.

    Attributes:
        label: local nickname (no whitespace, no ``:``, no ``#``).
        host: hostname or IP literal.
        port: TCP port (1..65535).
        pubkey: 32-byte X25519 static public key.
    """

    label: str
    host: str
    port: int
    pubkey: bytes

    def __post_init__(self) -> None:
        # Defensive: dataclass(frozen=True) but constructed directly — keep
        # the invariants that ``add`` would otherwise enforce.
        if not _LABEL_RE.match(self.label):
            raise ValueError(f"invalid label: {self.label!r}")
        if not self.host:
            raise ValueError("host must be non-empty")
        if not (1 <= self.port <= 65535):
            raise ValueError(f"port out of range: {self.port}")
        if not isinstance(self.pubkey, (bytes, bytearray)) or len(self.pubkey) != 32:
            raise ValueError("pubkey must be 32 bytes (X25519 static key)")

    @property
    def pubkey_hex(self) -> str:
        """Lowercase hex (64 chars) for serialization and error messages."""
        return self.pubkey.hex()

    def to_line(self) -> str:
        """Serialize to one ``known_servers`` file line (no trailing newline)."""
        return f"{self.label} {self.host}:{self.port} {self.pubkey_hex}"


# ---------- path / env helpers --------------------------------------------------


def config_dir() -> Path:
    """Return the directory the pinning store lives in.

    Honors ``TABULA_CONFIG_DIR`` if set (used by tests); otherwise defaults
    to ``~/.config/tabula``. The directory is *not* created here; writers
    create it as needed with mode 0700.
    """
    env = os.environ.get("TABULA_CONFIG_DIR")
    if env:
        return Path(env)
    return Path.home() / ".config" / "tabula"


def store_path() -> Path:
    """Return the absolute path to the ``known_servers`` file."""
    return config_dir() / _STORE_FILENAME


# ---------- parse / serialize ---------------------------------------------------


def _parse_pubkey_hex(hex_str: str) -> bytes:
    """Parse a 64-char lowercase hex pubkey to 32 bytes, or raise."""
    s = hex_str.strip()
    if not _HEX_PUBKEY_RE.match(s):
        raise InvalidPubkeyError(
            f"pubkey must be 64 lowercase hex chars (32 bytes), got {hex_str!r}"
        )
    return bytes.fromhex(s)


def _parse_host_port(token: str) -> tuple[str, int]:
    """Parse ``host:port`` into ``(host, port)``. IPv6 not supported in MVP."""
    # IPv6 with brackets is a future concern; MVP keeps the format simple.
    if ":" not in token:
        raise ValueError(f"expected host:port, got {token!r}")
    host, _, port_s = token.rpartition(":")
    if not host:
        raise ValueError(f"empty host in {token!r}")
    try:
        port = int(port_s)
    except ValueError as e:
        raise ValueError(f"non-integer port in {token!r}") from e
    if not (1 <= port <= 65535):
        raise ValueError(f"port out of range in {token!r}: {port}")
    return host, port


def _parse_line(line: str) -> ServerEntry | None:
    """Parse one file line. Returns None for blank/comment lines.

    Raises ``ValueError`` (or ``InvalidPubkeyError``) on malformed entries —
    callers in :func:`_read_all` catch and log-and-skip per the spec.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.split()
    if len(parts) != 3:
        raise ValueError(
            f"expected 3 whitespace-separated fields (label host:port pubkey), got {len(parts)}"
        )
    label, host_port, hex_pubkey = parts
    if not _LABEL_RE.match(label):
        raise ValueError(f"invalid label: {label!r}")
    host, port = _parse_host_port(host_port)
    pubkey = _parse_pubkey_hex(hex_pubkey)
    return ServerEntry(label=label, host=host, port=port, pubkey=pubkey)


def _read_all(path: Path) -> list[ServerEntry]:
    """Read all entries from ``path``.

    Missing file is treated as empty. Malformed lines are logged at WARNING
    and skipped — never crashes the caller.
    """
    if not path.exists():
        return []
    entries: list[ServerEntry] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            try:
                entry = _parse_line(raw)
            except (ValueError, InvalidPubkeyError) as e:
                log.warning(
                    "skipping malformed line %d in %s: %s",
                    lineno,
                    path,
                    e,
                )
                continue
            if entry is not None:
                entries.append(entry)
    return entries


def _ensure_parent_dir(path: Path) -> None:
    """Create the parent directory with mode 0700 if missing."""
    parent = path.parent
    if not parent.exists():
        parent.mkdir(parents=True, mode=_DIR_MODE)
        # On systems where the umask masked the mode bits, chmod explicitly.
        os.chmod(parent, _DIR_MODE)


def _atomic_write(path: Path, body: str) -> None:
    """Write ``body`` to ``path`` atomically with mode 0600.

    Writes to a temp file in the same directory, fsyncs, then renames over
    the target. The temp file is created with mode 0600 from the start so
    we never expose pubkeys with a wider mode (even though pubkeys are
    public — staying tidy on permissions is cheap).
    """
    _ensure_parent_dir(path)
    parent = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=".known_servers.", dir=str(parent))
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, _FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup; the original file (if any) is untouched.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    # ``os.replace`` preserves the source mode, but be paranoid on weird FSes.
    os.chmod(path, _FILE_MODE)


def _serialize(entries: list[ServerEntry], *, include_header: bool) -> str:
    """Serialize entries (and optional header) to file text."""
    out: list[str] = []
    if include_header:
        out.append(DEFAULT_STORE_HEADER)
    for entry in entries:
        out.append(entry.to_line())
    return "\n".join(out) + ("\n" if entries else "")


# ---------- public API ----------------------------------------------------------


def lookup(label: str) -> ServerEntry | None:
    """Resolve ``label`` to a :class:`ServerEntry`, or ``None`` if not pinned.

    Used by the chat connect path (#29) and the dialer (#27) to obtain
    ``(host, port, expected_pubkey)`` before initiating the Noise XX
    handshake.
    """
    if not isinstance(label, str) or not label:
        return None
    for entry in _read_all(store_path()):
        if entry.label == label:
            return entry
    return None


def list_all() -> list[ServerEntry]:
    """Return all pinned entries (in file order)."""
    return _read_all(store_path())


def add(
    label: str,
    host: str,
    port: int,
    pubkey: bytes,
    *,
    force: bool = False,
) -> ServerEntry:
    """Pin a server.

    Args:
        label: local nickname; must match ``[A-Za-z0-9._-]+``.
        host: hostname or IP.
        port: TCP port (1..65535).
        pubkey: 32-byte X25519 static public key.
        force: if True, replace an existing entry with the same label;
            otherwise raise :class:`DuplicateLabelError`.

    Returns:
        The :class:`ServerEntry` that was written.

    Raises:
        DuplicateLabelError: ``label`` already exists and ``force`` is False.
        InvalidPubkeyError: ``pubkey`` is not 32 bytes.
        ValueError: any other invariant (label shape, port range, host empty).
    """
    if not isinstance(pubkey, (bytes, bytearray)) or len(pubkey) != 32:
        raise InvalidPubkeyError("pubkey must be 32 bytes (X25519 static key)")

    new_entry = ServerEntry(
        label=label, host=host, port=port, pubkey=bytes(pubkey)
    )

    path = store_path()
    file_existed = path.exists()
    entries = _read_all(path)

    replaced = False
    out: list[ServerEntry] = []
    for existing in entries:
        if existing.label == label:
            if not force:
                raise DuplicateLabelError(label)
            # ``force``: drop the existing entry; new one will be appended.
            replaced = True
            continue
        out.append(existing)
    out.append(new_entry)

    body = _serialize(out, include_header=not file_existed)
    _atomic_write(path, body)
    log.info(
        "%s pinned server %r at %s:%d",
        "replaced" if replaced else "added",
        new_entry.label,
        new_entry.host,
        new_entry.port,
    )
    return new_entry


def remove(label: str) -> ServerEntry:
    """Remove the pinned entry for ``label``.

    Returns:
        The removed :class:`ServerEntry`.

    Raises:
        UnknownLabelError: ``label`` is not in the store.
    """
    path = store_path()
    entries = _read_all(path)

    removed: ServerEntry | None = None
    out: list[ServerEntry] = []
    for entry in entries:
        if entry.label == label and removed is None:
            removed = entry
            continue
        out.append(entry)

    if removed is None:
        raise UnknownLabelError(label)

    # Preserve the header on rewrite if the file already existed (it did,
    # since we just read entries from it). The simplest correct thing is to
    # always emit the header on rewrite; humans benefit from it staying.
    body = _serialize(out, include_header=True)
    _atomic_write(path, body)
    log.info("removed pinned server %r", label)
    return removed


# ---------- error message templates --------------------------------------------


def refuse_unknown_label_message(label: str) -> str:
    """Build the error text for the chat-connect refusal path.

    TOFU policy (locked): when the chat client is asked to connect to a
    label that is *not* in the pinning store, it refuses with this message.
    Centralized here so tests pin the wording and the chat UI doesn't drift.
    """
    return (
        f"unknown server label {label!r}: tabula refuses to silently TOFU.\n"
        f"To pin this server, run:\n"
        f"    tabula servers add {label} <host>:<port> <hex-pubkey>\n"
        f"Obtain <hex-pubkey> out-of-band (server admin / signed channel) — "
        f"do not accept it from the network."
    )


def mismatch_error_message(label: str, expected: bytes, offered: bytes) -> str:
    """Build the error text for a server pubkey mismatch on connect.

    Used by the dialer (#27) when the responder's offered static pubkey
    does not match the one pinned for ``label``. The message MUST include
    both keys in hex so a careful operator can compare them; it MUST
    suggest verification *before* any ``tabula servers remove`` + re-add.
    """
    expected_hex = expected.hex() if isinstance(expected, (bytes, bytearray)) else str(expected)
    offered_hex = offered.hex() if isinstance(offered, (bytes, bytearray)) else str(offered)
    return (
        f"server pubkey mismatch for label {label!r}:\n"
        f"  pinned   (expected): {expected_hex}\n"
        f"  offered  (received): {offered_hex}\n"
        f"Refusing the connection. This may indicate a server key rotation "
        f"OR an active man-in-the-middle attack.\n"
        f"VERIFY THE NEW KEY OUT-OF-BAND before doing anything. Once verified:\n"
        f"    tabula servers remove {label}\n"
        f"    tabula servers add {label} <host>:<port> {offered_hex}\n"
        f"Do not blindly re-pin."
    )
