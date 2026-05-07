"""Minimal Noise XX wrapper over ``dissononce``.

This is the contract #18 will own. We implement the smallest version needed
for #27 (the client dialer) and #20 (the server listener) to share a real,
byte-format-equivalent handshake. When #18 lands it will replace this file
with a hardened version; the public API here is the contract.

Cipher suite: ``Noise_XX_25519_ChaChaPoly_BLAKE2s``.

Public API:
- :func:`generate_keypair` -> ``KeyPair`` (X25519 static keypair).
- ``XXInitiator(local_static_key, remote_static_pubkey=None)``.
- ``XXResponder(local_static_key)``.
- Both expose ``write_message(payload) -> bytes`` and
  ``read_message(ciphertext) -> bytes`` for handshake messages, and
  ``encrypt(plaintext) -> bytes`` / ``decrypt(ciphertext) -> bytes`` for the
  transport phase. ``handshake_finished`` flips True once both have run all
  three XX messages. ``remote_static_pubkey`` exposes the post-handshake
  public key the pinning layer in #32 will check.
"""

from __future__ import annotations

from dataclasses import dataclass

from dissononce.cipher.chachapoly import ChaChaPolyCipher
from dissononce.dh.x25519.x25519 import X25519DH
from dissononce.hash.blake2s import Blake2sHash
from dissononce.processing.handshakepatterns.interactive.XX import (
    XXHandshakePattern,
)
from dissononce.processing.impl.cipherstate import CipherState
from dissononce.processing.impl.handshakestate import HandshakeState
from dissononce.processing.impl.symmetricstate import SymmetricState


class NoiseError(Exception):
    """Generic Noise wrapper error."""


class KeyMismatchError(NoiseError):
    """Raised when an expected remote static pubkey does not match."""


@dataclass
class KeyPair:
    """Wrapper around a dissononce X25519 keypair.

    The underlying object is whatever dissononce hands back from
    ``X25519DH().generate_keypair()`` — we just stash it. The ``public`` byte
    accessor is exposed for callers that need to compare or display.
    """

    _impl: object  # dissononce KeyPair

    @property
    def public_bytes(self) -> bytes:
        return bytes(self._impl.public.data)


def generate_keypair() -> KeyPair:
    """Generate a fresh X25519 static keypair."""
    return KeyPair(_impl=X25519DH().generate_keypair())


def _new_handshakestate() -> HandshakeState:
    return HandshakeState(
        SymmetricState(CipherState(ChaChaPolyCipher()), Blake2sHash()),
        X25519DH(),
    )


class _XXBase:
    """Shared behaviour for initiator and responder."""

    def __init__(self) -> None:
        self._hs: HandshakeState | None = _new_handshakestate()
        self._send_cs: CipherState | None = None
        self._recv_cs: CipherState | None = None
        self._handshake_finished = False
        self._remote_static_pubkey: bytes | None = None

    @property
    def handshake_finished(self) -> bool:
        return self._handshake_finished

    @property
    def remote_static_pubkey(self) -> bytes | None:
        """Bytes of the peer's static public key, available after handshake."""
        return self._remote_static_pubkey

    def _capture_split(self, split: object) -> None:
        # dissononce returns (cipherstate1, cipherstate2) on the message that
        # completes the handshake. Order is initiator-send / initiator-recv,
        # which from the responder's perspective is recv / send.
        cs1, cs2 = split  # type: ignore[misc]
        self._send_cs, self._recv_cs = self._split_cs_for_role(cs1, cs2)
        self._handshake_finished = True
        # Pull rs out of the handshake state before discarding it.
        if self._hs is not None and self._hs.rs is not None:
            self._remote_static_pubkey = bytes(self._hs.rs.data)
        self._hs = None  # let it gc

    def _split_cs_for_role(
        self, cs1: CipherState, cs2: CipherState
    ) -> tuple[CipherState, CipherState]:
        raise NotImplementedError

    def encrypt(self, plaintext: bytes) -> bytes:
        if not self._handshake_finished or self._send_cs is None:
            raise NoiseError("encrypt called before handshake completed")
        return bytes(self._send_cs.encrypt_with_ad(b"", plaintext))

    def decrypt(self, ciphertext: bytes) -> bytes:
        if not self._handshake_finished or self._recv_cs is None:
            raise NoiseError("decrypt called before handshake completed")
        try:
            return bytes(self._recv_cs.decrypt_with_ad(b"", ciphertext))
        except Exception as exc:
            raise NoiseError(f"decrypt failed: {exc}") from exc


class XXInitiator(_XXBase):
    """Noise XX initiator. Writes msg1 and msg3, reads msg2.

    Args:
        local_static_key: caller-supplied X25519 static keypair (the result
            of :func:`generate_keypair`).
        remote_static_pubkey: optional pinned bytes for the responder static
            pubkey. When provided, :meth:`verify_remote_static_pubkey` can be
            called after the handshake; this wrapper does **not** auto-verify
            because the dialer in #27 wants to control failure timing.
    """

    def __init__(
        self,
        local_static_key: KeyPair,
        remote_static_pubkey: bytes | None = None,
    ) -> None:
        super().__init__()
        self._expected_remote_static_pubkey = remote_static_pubkey
        assert self._hs is not None
        self._hs.initialize(
            XXHandshakePattern(),
            True,  # initiator
            b"",  # prologue
            s=local_static_key._impl,
        )

    def _split_cs_for_role(
        self, cs1: CipherState, cs2: CipherState
    ) -> tuple[CipherState, CipherState]:
        # Initiator: cs1 = send, cs2 = recv.
        return cs1, cs2

    def write_message(self, payload: bytes = b"") -> bytes:
        if self._hs is None:
            raise NoiseError("handshake already finished; use encrypt() instead")
        buf = bytearray()
        result = self._hs.write_message(payload, buf)
        if result is not None:
            self._capture_split(result)
        return bytes(buf)

    def read_message(self, ciphertext: bytes) -> bytes:
        if self._hs is None:
            raise NoiseError("handshake already finished; use decrypt() instead")
        out = bytearray()
        try:
            result = self._hs.read_message(ciphertext, out)
        except Exception as exc:
            raise NoiseError(f"handshake read failed: {exc}") from exc
        if result is not None:
            self._capture_split(result)
        return bytes(out)

    def verify_remote_static_pubkey(self, expected: bytes) -> None:
        """Raise :class:`KeyMismatchError` if the responder's static pubkey
        does not match ``expected``."""
        if self._remote_static_pubkey is None:
            raise NoiseError("handshake has not completed")
        if self._remote_static_pubkey != expected:
            raise KeyMismatchError(
                "responder static pubkey does not match pinned value"
            )


class XXResponder(_XXBase):
    """Noise XX responder. Reads msg1 and msg3, writes msg2."""

    def __init__(self, local_static_key: KeyPair) -> None:
        super().__init__()
        assert self._hs is not None
        self._hs.initialize(
            XXHandshakePattern(),
            False,  # responder
            b"",
            s=local_static_key._impl,
        )

    def _split_cs_for_role(
        self, cs1: CipherState, cs2: CipherState
    ) -> tuple[CipherState, CipherState]:
        # Responder: cs1 = recv (initiator-send), cs2 = send (initiator-recv).
        return cs2, cs1

    def write_message(self, payload: bytes = b"") -> bytes:
        if self._hs is None:
            raise NoiseError("handshake already finished; use encrypt() instead")
        buf = bytearray()
        result = self._hs.write_message(payload, buf)
        if result is not None:
            self._capture_split(result)
        return bytes(buf)

    def read_message(self, ciphertext: bytes) -> bytes:
        if self._hs is None:
            raise NoiseError("handshake already finished; use decrypt() instead")
        out = bytearray()
        try:
            result = self._hs.read_message(ciphertext, out)
        except Exception as exc:
            raise NoiseError(f"handshake read failed: {exc}") from exc
        if result is not None:
            self._capture_split(result)
        return bytes(out)


__all__ = [
    "KeyPair",
    "KeyMismatchError",
    "NoiseError",
    "XXInitiator",
    "XXResponder",
    "generate_keypair",
]
