"""Unit tests for the e2e harness pieces in ``conftest.py``.

These tests do **not** depend on the wire stack (#16/#18/#20/#22/#25/#27/#29/
#31/#32). They exercise the standalone helpers — fake-claude fixture, socket
tee, ephemeral-port allocator, and X25519 keypair generator — so a regression
in the harness surfaces immediately rather than being masked by skipped
end-to-end runs while the wire stack is still in flight.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from tests.integration.conftest import (
    SocketTee,
    ephemeral_port,
    generate_x25519_keypair,
)


# ---------------------------------------------------------------------------
# fake_claude.sh
# ---------------------------------------------------------------------------


def _delta_text(event: dict) -> str | None:
    """Extract the delta text from a claude ``stream_event`` line, or None."""
    if event.get("type") != "stream_event":
        return None
    inner = event.get("event") or {}
    if inner.get("type") != "content_block_delta":
        return None
    delta = inner.get("delta") or {}
    if delta.get("type") != "text_delta":
        return None
    return delta.get("text")


class TestFakeClaudeFixture:
    def test_streams_default_response_tokens(self, fake_claude_path: Path) -> None:
        proc = subprocess.run(
            [str(fake_claude_path)],
            input="hello\n",
            capture_output=True,
            text=True,
            timeout=10,
            env={"FAKE_CLAUDE_TOKEN_DELAY_MS": "0", "PATH": "/bin:/usr/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        events = [
            json.loads(ln)
            for ln in proc.stdout.splitlines()
            if ln.strip()
        ]
        # Canonical claude stream-json: one system/init line, then per-turn:
        # multiple content_block_delta events, then one result event.
        assert any(
            e.get("type") == "system" and e.get("subtype") == "init"
            for e in events
        ), "expected initial system/init line"
        result_events = [
            e for e in events
            if e.get("type") == "result" and e.get("subtype") == "success"
        ]
        assert len(result_events) == 1, "expected exactly one result/success per turn"
        deltas = [t for e in events if (t := _delta_text(e)) is not None]
        assert len(deltas) >= 2, "expected multiple streamed text deltas"
        joined = "-".join(deltas)
        assert "zephyr" in joined and "glissando" in joined

    def test_response_phrase_is_overridable(self, fake_claude_path: Path) -> None:
        proc = subprocess.run(
            [str(fake_claude_path)],
            input="anything\n",
            capture_output=True,
            text=True,
            timeout=10,
            env={
                "FAKE_CLAUDE_TOKEN_DELAY_MS": "0",
                "FAKE_CLAUDE_RESPONSE": "alpha-bravo-charlie",
                "PATH": "/bin:/usr/bin",
            },
        )
        assert proc.returncode == 0, proc.stderr
        events = [
            json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()
        ]
        deltas = [t for e in events if (t := _delta_text(e)) is not None]
        assert deltas == ["alpha", "bravo", "charlie"]


# ---------------------------------------------------------------------------
# ephemeral_port
# ---------------------------------------------------------------------------


def test_ephemeral_port_returns_distinct_high_ports() -> None:
    p1 = ephemeral_port()
    p2 = ephemeral_port()
    assert 1024 <= p1 <= 65535
    assert 1024 <= p2 <= 65535
    # Not strictly required, but the OS almost never re-issues the same port
    # back-to-back. If this flakes we should rethink the helper.
    assert p1 != p2


# ---------------------------------------------------------------------------
# SocketTee
# ---------------------------------------------------------------------------


def _start_echo_server(host: str = "127.0.0.1") -> tuple[socket.socket, int, threading.Event]:
    """Start a tiny synchronous echo server on an ephemeral port."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, 0))
    srv.listen(4)
    srv.settimeout(0.25)
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(0.25)
                while not stop.is_set():
                    try:
                        data = conn.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        return
                    if not data:
                        return
                    try:
                        conn.sendall(data)
                    except OSError:
                        return

    threading.Thread(target=loop, daemon=True).start()
    return srv, srv.getsockname()[1], stop


class TestSocketTee:
    def test_records_traffic_in_both_directions(self) -> None:
        srv, srv_port, stop = _start_echo_server()
        try:
            with SocketTee(upstream_port=srv_port) as tee:
                client = socket.create_connection(("127.0.0.1", tee.bound_port), timeout=2.0)
                try:
                    payload = b"plaintext-needle-12345"
                    client.sendall(payload)
                    # Read the echo back.
                    client.settimeout(2.0)
                    received = b""
                    while len(received) < len(payload):
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        received += chunk
                    assert received == payload
                finally:
                    client.close()
                # Allow the tee's pump threads to flush.
                import time

                time.sleep(0.2)
                assert payload in bytes(tee.client_to_server)
                assert payload in bytes(tee.server_to_client)
        finally:
            stop.set()
            srv.close()

    def test_assert_no_plaintext_detects_leak(self) -> None:
        srv, srv_port, stop = _start_echo_server()
        try:
            with SocketTee(upstream_port=srv_port) as tee:
                client = socket.create_connection(("127.0.0.1", tee.bound_port), timeout=2.0)
                try:
                    client.sendall(b"secret-sauce")
                    client.settimeout(2.0)
                    _ = client.recv(4096)
                finally:
                    client.close()
                import time

                time.sleep(0.2)
                with pytest.raises(AssertionError):
                    tee.assert_no_plaintext("secret-sauce")
        finally:
            stop.set()
            srv.close()

    def test_assert_no_plaintext_passes_when_clean(self) -> None:
        srv, srv_port, stop = _start_echo_server()
        try:
            with SocketTee(upstream_port=srv_port) as tee:
                client = socket.create_connection(("127.0.0.1", tee.bound_port), timeout=2.0)
                try:
                    client.sendall(b"\x00\x01\x02\x03ciphertext-bytes")
                    client.settimeout(2.0)
                    _ = client.recv(4096)
                finally:
                    client.close()
                import time

                time.sleep(0.2)
                # Needle never appears, so the assertion passes.
                tee.assert_no_plaintext("zephyr-quokka-prism-mango-vortex-glissando")
        finally:
            stop.set()
            srv.close()


# ---------------------------------------------------------------------------
# generate_x25519_keypair
# ---------------------------------------------------------------------------


def test_generate_x25519_keypair_returns_32_byte_pair() -> None:
    sk, pk = generate_x25519_keypair()
    assert isinstance(sk, bytes) and isinstance(pk, bytes)
    assert len(sk) == 32, f"expected 32-byte X25519 private key, got {len(sk)}"
    assert len(pk) == 32, f"expected 32-byte X25519 public key, got {len(pk)}"
    # Two calls must produce distinct keys.
    sk2, pk2 = generate_x25519_keypair()
    assert sk != sk2
    assert pk != pk2
