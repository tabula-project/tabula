"""NON-CRYPTOGRAPHIC stand-in for the Noise XX responder.

Purpose
-------
Issue #20 (this module's parent) lands in parallel with #18 (real
Noise XX wrapper). The listener needs *some* implementation of
``NoiseResponderProtocol`` to be testable end-to-end before #18 is
merged.

This stub is intentionally trivial:

- Three-message handshake just like XX, but every message is a
  fixed-format payload — no DH, no AEAD.
- ``encrypt`` / ``decrypt`` are XOR with a deterministic keystream
  derived from the responder's "static key". Nothing about this is
  secure; it exists only to verify framing + state-machine plumbing
  in :mod:`wire.server.listener`.
- Authentication is enforced via a fixed 16-byte trailer that mirrors
  AEAD tag semantics (so we can test "bad ciphertext fails decrypt").

Integration with #18
--------------------
When the real Noise XX wrapper from #18 lands:

1. Replace imports of ``XXResponderStub`` with the real
   ``XXResponder`` from ``wire.crypto.noise_xx``.
2. Drop this file.
3. Tests in ``wire/server/tests/test_listener.py`` parametrize over
   the responder factory; only the factory needs to change.

The listener never imports the stub directly — it accepts a factory
function — so no listener code needs to change.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .protocol import HandshakeError, NoiseResponderProtocol, NoiseTransportProtocol

_HANDSHAKE_TAG_INIT_E = b"STUB:init_e:"
_HANDSHAKE_TAG_RESP_E_S = b"STUB:resp_e_s:"
_HANDSHAKE_TAG_INIT_S = b"STUB:init_s:"
_AUTH_TRAILER = b"STUB-AUTH-TAG-OK"  # 16 bytes, mirrors AEAD tag size


def _keystream(key: bytes, length: int, counter: int) -> bytes:
    out = bytearray()
    block = b""
    i = 0
    while len(out) < length:
        block = hashlib.blake2b(
            key + counter.to_bytes(8, "big") + i.to_bytes(8, "big"),
            digest_size=64,
        ).digest()
        out.extend(block)
        i += 1
    return bytes(out[:length])


def _xor(data: bytes, ks: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, ks, strict=True))


@dataclass
class _StubTransport(NoiseTransportProtocol):
    """In-memory non-crypto transport for testing."""

    _send_key: bytes
    _recv_key: bytes
    _remote_static_pubkey: bytes
    _send_counter: int = 0
    _recv_counter: int = 0

    def encrypt(self, plaintext: bytes) -> bytes:
        ks = _keystream(self._send_key, len(plaintext), self._send_counter)
        self._send_counter += 1
        return _xor(plaintext, ks) + _AUTH_TRAILER

    def decrypt(self, ciphertext: bytes) -> bytes:
        if len(ciphertext) < len(_AUTH_TRAILER):
            raise HandshakeError("ciphertext too short for auth trailer")
        body, trailer = ciphertext[:-len(_AUTH_TRAILER)], ciphertext[-len(_AUTH_TRAILER):]
        if trailer != _AUTH_TRAILER:
            raise HandshakeError("auth trailer mismatch")
        ks = _keystream(self._recv_key, len(body), self._recv_counter)
        self._recv_counter += 1
        return _xor(body, ks)

    @property
    def remote_static_pubkey(self) -> bytes:
        return self._remote_static_pubkey


class XXResponderStub(NoiseResponderProtocol):
    """Stub XX responder. NON-CRYPTOGRAPHIC. See module docstring."""

    def __init__(self, local_static_key: bytes) -> None:
        if len(local_static_key) != 32:
            # Same constraint shape as real X25519, helps tests catch
            # accidental truncation bugs.
            raise ValueError("local_static_key must be 32 bytes")
        self._local_static = local_static_key
        self._step = 0  # 0=expect msg1, 1=must write msg2, 2=expect msg3, 3=done
        self._remote_static_pubkey: bytes | None = None

    @property
    def handshake_finished(self) -> bool:
        return self._step == 3

    def read_handshake_message(self, ciphertext: bytes) -> bytes:
        if self._step == 0:
            if not ciphertext.startswith(_HANDSHAKE_TAG_INIT_E):
                raise HandshakeError("malformed XX msg1")
            self._step = 1
            return ciphertext[len(_HANDSHAKE_TAG_INIT_E):]
        if self._step == 2:
            if not ciphertext.startswith(_HANDSHAKE_TAG_INIT_S):
                raise HandshakeError("malformed XX msg3")
            tail = ciphertext[len(_HANDSHAKE_TAG_INIT_S):]
            if len(tail) < 32:
                raise HandshakeError("XX msg3 missing initiator static key")
            self._remote_static_pubkey = tail[:32]
            self._step = 3
            return tail[32:]
        raise HandshakeError(f"read_handshake_message in step {self._step}")

    def write_handshake_message(self, payload: bytes) -> bytes:
        if self._step != 1:
            raise HandshakeError(f"write_handshake_message in step {self._step}")
        out = _HANDSHAKE_TAG_RESP_E_S + self._local_static + payload
        self._step = 2
        return out

    def into_transport(self) -> NoiseTransportProtocol:
        if not self.handshake_finished:
            raise HandshakeError("handshake not complete")
        assert self._remote_static_pubkey is not None
        # Derive distinct send/recv keys. Sort the two static keys so
        # the initiator and responder produce the same digest.
        a, b = sorted([self._local_static, self._remote_static_pubkey])
        digest = hashlib.blake2b(a + b, digest_size=64).digest()
        # Responder receives with key A, sends with key B. Initiator
        # mirrors (sends with A, receives with B).
        return _StubTransport(
            _send_key=digest[32:],
            _recv_key=digest[:32],
            _remote_static_pubkey=self._remote_static_pubkey,
        )


class XXInitiatorStub:
    """Stub XX initiator, used only in tests to drive the responder.

    Mirrors :class:`XXResponderStub`. NON-CRYPTOGRAPHIC.
    """

    def __init__(self, local_static_key: bytes, expected_remote_pubkey: bytes | None = None) -> None:
        if len(local_static_key) != 32:
            raise ValueError("local_static_key must be 32 bytes")
        self._local_static = local_static_key
        self._expected_remote = expected_remote_pubkey
        self._remote_static_pubkey: bytes | None = None
        self._step = 0  # 0=write msg1, 1=read msg2, 2=write msg3, 3=done

    @property
    def handshake_finished(self) -> bool:
        return self._step == 3

    def write_handshake_message(self, payload: bytes = b"") -> bytes:
        if self._step == 0:
            self._step = 1
            return _HANDSHAKE_TAG_INIT_E + payload
        if self._step == 2:
            self._step = 3
            return _HANDSHAKE_TAG_INIT_S + self._local_static + payload
        raise HandshakeError(f"initiator write in step {self._step}")

    def read_handshake_message(self, ciphertext: bytes) -> bytes:
        if self._step != 1:
            raise HandshakeError(f"initiator read in step {self._step}")
        if not ciphertext.startswith(_HANDSHAKE_TAG_RESP_E_S):
            raise HandshakeError("malformed XX msg2")
        tail = ciphertext[len(_HANDSHAKE_TAG_RESP_E_S):]
        if len(tail) < 32:
            raise HandshakeError("XX msg2 missing responder static key")
        self._remote_static_pubkey = tail[:32]
        if self._expected_remote is not None and self._remote_static_pubkey != self._expected_remote:
            raise HandshakeError("server static key did not match pin")
        self._step = 2
        return tail[32:]

    def into_transport(self) -> NoiseTransportProtocol:
        if not self.handshake_finished:
            raise HandshakeError("handshake not complete")
        assert self._remote_static_pubkey is not None
        # Same sorted-keys derivation as XXResponderStub so both sides
        # arrive at identical send/recv key material.
        a, b = sorted([self._local_static, self._remote_static_pubkey])
        digest = hashlib.blake2b(a + b, digest_size=64).digest()
        # Mirror responder: initiator sends with key A, receives with key B.
        return _StubTransport(
            _send_key=digest[:32],
            _recv_key=digest[32:],
            _remote_static_pubkey=self._remote_static_pubkey,
        )
