"""Test harness for the localhost e2e integration test (#34).

This module provides reusable pieces that exercise the wire stack end-to-end on
a single host:

  - ``ephemeral_port``: bind to port 0, read back the assigned port, return it.
  - ``SocketTee``: a tiny TCP forwarder that records every byte that passes
    through in either direction. The integration test routes the client's
    socket through this so it can later assert that no plaintext substring of
    the prompt or response appears on the wire.
  - ``fake_claude_path``: absolute path to the deterministic fake-claude shell
    fixture under ``tests/integration/fixtures/fake_claude.sh``.
  - ``generate_x25519_keypair``: thin wrapper that prefers the project's
    ``tabula_wire.crypto.keys`` helper (see #31, canonical layout from #57)
    when it is importable, and otherwise falls back to ``cryptography``'s
    raw X25519 primitives. Tests that need real wire crypto skip themselves
    when neither is available.

The harness deliberately does **not** import any tabula_wire / tabula_cli
symbols at module load time. The dependency chain (#16, #18, #20, #22, #25,
#27, #29, #31, #32) is still in flight; importing eagerly would make this
file fail to collect on a clean checkout. Instead, every helper either does
its work without the wire stack or raises ``pytest.skip`` at call time.
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
from contextlib import closing
from pathlib import Path
from typing import Callable

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_FIXTURES = _HERE / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Absolute path to the integration fixtures directory."""
    return _FIXTURES


@pytest.fixture(scope="session")
def fake_claude_path(fixtures_dir: Path) -> Path:
    """Absolute path to the deterministic fake-claude shell fixture."""
    p = fixtures_dir / "fake_claude.sh"
    if not p.exists():
        pytest.fail(f"fake_claude fixture missing at {p}")
    if not os.access(p, os.X_OK):
        # Make it executable on systems that strip the bit (e.g. some CI checkouts).
        p.chmod(0o755)
    return p


# ---------------------------------------------------------------------------
# Ephemeral ports
# ---------------------------------------------------------------------------


def ephemeral_port() -> int:
    """Bind to port 0 on localhost, read back the assigned port, then close.

    The port is briefly free between this call and whoever listens on it next.
    On a single-host integration test this race is acceptable; we never use a
    hardcoded port.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Socket tee
# ---------------------------------------------------------------------------


class SocketTee:
    """Tiny localhost TCP forwarder that records every byte in both directions.

    Listens on ``listen_port`` and forwards each accepted connection to
    ``upstream_host:upstream_port``. As bytes flow in either direction they are
    appended to ``self.client_to_server`` and ``self.server_to_client``
    respectively, so the test can later search the full transcript for known
    plaintext substrings.

    The tee is intentionally synchronous and blocking. It runs each direction
    on a daemon thread and shuts down cleanly when ``close()`` is called.
    """

    def __init__(
        self,
        listen_host: str = "127.0.0.1",
        listen_port: int | None = None,
        upstream_host: str = "127.0.0.1",
        upstream_port: int = 0,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port if listen_port is not None else 0
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port

        self.client_to_server = bytearray()
        self.server_to_client = bytearray()

        self._lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def bound_port(self) -> int:
        """The actual port the tee is listening on (resolves port 0)."""
        if self._listener is None:
            raise RuntimeError("SocketTee not started")
        return self._listener.getsockname()[1]

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.listen_host, self.listen_port))
        sock.listen(8)
        sock.settimeout(0.25)
        self._listener = sock

        t = threading.Thread(target=self._accept_loop, name="tee-accept", daemon=True)
        t.start()
        self._threads.append(t)

    def close(self) -> None:
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        for t in self._threads:
            t.join(timeout=1.0)

    # context-manager sugar
    def __enter__(self) -> "SocketTee":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----- internals -----

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                client, _addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                upstream = socket.create_connection(
                    (self.upstream_host, self.upstream_port), timeout=2.0
                )
            except OSError:
                client.close()
                continue
            for src, dst, sink in (
                (client, upstream, self.client_to_server),
                (upstream, client, self.server_to_client),
            ):
                t = threading.Thread(
                    target=self._pump,
                    args=(src, dst, sink),
                    daemon=True,
                    name="tee-pump",
                )
                t.start()
                self._threads.append(t)

    def _pump(self, src: socket.socket, dst: socket.socket, sink: bytearray) -> None:
        src.settimeout(0.25)
        try:
            while not self._stop.is_set():
                try:
                    chunk = src.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                with self._lock:
                    sink.extend(chunk)
                try:
                    dst.sendall(chunk)
                except OSError:
                    return
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass

    # ----- assertions -----

    def assert_no_plaintext(self, *needles: bytes | str) -> None:
        """Fail if any needle appears in either direction of the transcript."""
        with self._lock:
            full = bytes(self.client_to_server) + b"\n--SPLIT--\n" + bytes(self.server_to_client)
        for needle in needles:
            n = needle.encode() if isinstance(needle, str) else needle
            assert n not in full, (
                f"plaintext leak: {n!r} appeared on the wire "
                f"(c2s={len(self.client_to_server)}B, s2c={len(self.server_to_client)}B)"
            )


# ---------------------------------------------------------------------------
# X25519 keypair generation (with graceful fallback)
# ---------------------------------------------------------------------------


def generate_x25519_keypair() -> tuple[bytes, bytes]:
    """Return ``(private_bytes, public_bytes)`` for a fresh X25519 keypair.

    Prefers the project's ``tabula_wire.crypto.keys`` helper (#31) when it is
    importable; falls back to ``cryptography``'s raw X25519 primitives so this
    helper still works on a clean checkout where the wire stack hasn't merged
    yet. Skips the calling test if neither is available.
    """
    try:  # pragma: no cover - prefers project helper when present
        from tabula_wire.crypto import keys as _keys  # type: ignore[import-not-found]

        if hasattr(_keys, "generate_keypair"):
            sk, pk = _keys.generate_keypair()
            return bytes(sk), bytes(pk)
    except Exception:
        pass

    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            PublicFormat,
        )
    except Exception:  # pragma: no cover - environment without cryptography
        pytest.skip("neither tabula_wire.crypto.keys nor cryptography is importable")

    sk = X25519PrivateKey.generate()
    pk = sk.public_key()
    return (
        sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()),
        pk.public_bytes(Encoding.Raw, PublicFormat.Raw),
    )


# ---------------------------------------------------------------------------
# Wire-stack availability
# ---------------------------------------------------------------------------


def _wire_stack_available() -> tuple[bool, str]:
    """Probe whether the canonical wire stack is importable.

    Returns ``(ok, reason)``. The integration test uses this to decide whether
    to run the full end-to-end flow or skip with a clear message. The probe is
    intentionally tolerant: it only requires that *some* canonical entrypoints
    exist, so a partial merge (e.g. just framing + crypto) still surfaces a
    useful skip reason rather than a cryptic ImportError.
    """
    required = (
        "tabula_wire.framing",
        "tabula_wire.crypto.noise",
        "tabula_wire.crypto.keys",
        "tabula_wire.proto.v1",
        "tabula_wire.server.listener",
        "tabula_wire.server.session",
        "tabula_wire.client.dialer",
        "tabula_wire.client.pinning",
    )
    import importlib

    missing: list[str] = []
    for name in required:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001
            missing.append(name)
    if missing:
        return False, "wire stack not yet available; missing: " + ", ".join(missing)
    return True, ""


@pytest.fixture(scope="session")
def wire_stack_available() -> bool:
    ok, _ = _wire_stack_available()
    return ok


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def wait_for(
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
    interval: float = 0.05,
    description: str = "predicate",
) -> None:
    """Poll ``predicate`` until it returns truthy, or raise on timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise asyncio.TimeoutError(f"{description} did not become true within {timeout}s")
        await asyncio.sleep(interval)
