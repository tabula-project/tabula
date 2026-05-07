"""Noise XX wrappers: XXInitiator / XXResponder.

A thin, opinionated state machine around the `dissononce` library. The wrapper
hides the cipher-suite plumbing (``Noise_XX_25519_ChaChaPoly_BLAKE2s`` is the
only suite supported here — see ``wire/README.md``) and exposes a small surface
that the server/client frame loops will consume.

Static keys come from :mod:`tabula_wire.crypto.keys` (PR #46): the wrapper
accepts a :class:`~tabula_wire.crypto.keys.SecretKey` for the local static and
a :class:`~tabula_wire.crypto.keys.PublicKey` (or its raw 32-byte form) for the
optional pin on the responder's static. We do **not** redefine key types here.

Lifecycle:

    write_handshake_message() / read_handshake_message()
        — drive the 3-message XX exchange. The caller is responsible for I/O.
        — initiator pattern: write, read, write
        — responder pattern: read, write, read
        — ``handshake_finished`` flips True once the final exchange completes.

    encrypt() / decrypt()
        — only valid AFTER ``handshake_finished``. Each call advances the
          per-direction nonce. AD is empty for now (the proto frame layer in
          #16 will add its own integrity if it wants).

    remote_static_pubkey
        — the 32-byte X25519 public key the peer presented during the
          handshake. Pinning logic (sub-issue #32) compares this to the
          out-of-band-trusted pubkey.
"""

from __future__ import annotations

from typing import Optional, Union

from dissononce.cipher.chachapoly import ChaChaPolyCipher
from dissononce.dh.keypair import KeyPair as _DissKeyPair
from dissononce.dh.x25519.private import PrivateKey as _DissPrivateKey
from dissononce.dh.x25519.public import PublicKey as _DissPublicKey
from dissononce.hash.blake2s import Blake2sHash
from dissononce.dh.x25519.x25519 import X25519DH
from dissononce.processing.handshakepatterns.interactive.XX import (
    XXHandshakePattern,
)
from dissononce.processing.impl.cipherstate import CipherState
from dissononce.processing.impl.handshakestate import HandshakeState
from dissononce.processing.impl.symmetricstate import SymmetricState

from tabula_wire.crypto.keys import PUBLIC_KEY_BYTES, PublicKey, SecretKey
from tabula_wire.errors import HandshakeError

__all__ = ["HandshakeError", "XXInitiator", "XXResponder"]


def _new_handshake_state() -> HandshakeState:
    """Build a fresh ``Noise_XX_25519_ChaChaPoly_BLAKE2s`` HandshakeState.

    Cipher suite is hard-coded: see ``wire/README.md`` for rationale.
    """
    return HandshakeState(
        SymmetricState(CipherState(ChaChaPolyCipher()), Blake2sHash()),
        X25519DH(),
    )


def _secret_to_diss_keypair(secret: SecretKey) -> _DissKeyPair:
    """Convert a canonical :class:`SecretKey` to a dissononce ``KeyPair``.

    dissononce expects its own key types internally. We materialize them from
    the raw 32-byte X25519 scalar and the derived public bytes — no extra
    randomness, no reformatting of secret material on disk.
    """
    raw_priv = bytes(secret.raw)
    raw_pub = secret.public_key().raw
    return _DissKeyPair(
        public_key=_DissPublicKey(raw_pub),
        private_key=_DissPrivateKey(raw_priv),
    )


def _coerce_pin(
    pin: Union[PublicKey, bytes, bytearray, None],
) -> Optional[bytes]:
    """Normalize an optional pubkey pin to 32 raw bytes (or None)."""
    if pin is None:
        return None
    if isinstance(pin, PublicKey):
        return bytes(pin.raw)
    if isinstance(pin, (bytes, bytearray)):
        if len(pin) != PUBLIC_KEY_BYTES:
            raise ValueError(
                f"remote_static_pubkey must be {PUBLIC_KEY_BYTES} bytes, "
                f"got {len(pin)}"
            )
        return bytes(pin)
    raise TypeError(
        "remote_static_pubkey must be PublicKey, bytes, or None; "
        f"got {type(pin).__name__}"
    )


class _XXBase:
    """Shared lifecycle for initiator + responder.

    Subclasses set ``_initiator: bool`` and call ``_initialize()`` from
    ``__init__``.
    """

    _initiator: bool

    def __init__(self, local_static: SecretKey) -> None:
        if not isinstance(local_static, SecretKey):
            raise TypeError(
                "local_static must be tabula_wire.crypto.keys.SecretKey, "
                f"got {type(local_static).__name__}"
            )
        self._local_static = local_static
        self._diss_keypair = _secret_to_diss_keypair(local_static)
        self._hs: Optional[HandshakeState] = _new_handshake_state()
        self._cs_send: Optional[CipherState] = None
        self._cs_recv: Optional[CipherState] = None
        self._remote_static_pubkey: Optional[bytes] = None
        # Subclass calls _initialize() once it knows initiator/responder role.

    def _initialize(self) -> None:
        assert self._hs is not None
        self._hs.initialize(
            handshake_pattern=XXHandshakePattern(),
            initiator=self._initiator,
            prologue=b"",
            s=self._diss_keypair,
        )

    @property
    def handshake_finished(self) -> bool:
        """True once the 3-message XX exchange has produced transport keys."""
        return self._cs_send is not None and self._cs_recv is not None

    @property
    def remote_static_pubkey(self) -> Optional[bytes]:
        """The 32-byte X25519 pubkey the peer transmitted during XX.

        ``None`` until the handshake has progressed far enough for the peer's
        static to have been received and decrypted. After
        ``handshake_finished``, this is guaranteed non-None.
        """
        return self._remote_static_pubkey

    def _capture_remote_static(self) -> None:
        """If the peer's static is now known, snapshot the 32-byte form."""
        if self._hs is not None and self._hs.rs is not None:
            self._remote_static_pubkey = bytes(self._hs.rs.data)

    def _set_transport_keys(self, cs_pair) -> None:  # type: ignore[no-untyped-def]
        """Install the (send, recv) CipherState pair after handshake.

        Per Noise spec for XX (initiator pattern):
          - Initiator: cs_pair[0] is initiator->responder send
                       cs_pair[1] is responder->initiator recv
          - Responder: cs_pair[0] is initiator->responder recv
                       cs_pair[1] is responder->initiator send
        """
        cs1, cs2 = cs_pair
        if self._initiator:
            self._cs_send, self._cs_recv = cs1, cs2
        else:
            self._cs_recv, self._cs_send = cs1, cs2
        # After final handshake message, remote static is definitely available.
        self._capture_remote_static()
        # Drop the handshake state — it's no longer needed and holds key material.
        self._hs = None

    # ---- Handshake phase ----

    def write_handshake_message(self, payload: bytes = b"") -> bytes:
        """Produce the next outgoing handshake message.

        Caller is responsible for transmitting the returned bytes. Most XX
        deployments send empty payload bytes; the parameter is kept for spec
        parity (XX allows piggy-backed payload after the relevant keys
        arrive).
        """
        if self._hs is None:
            raise HandshakeError("handshake already finished; use encrypt()")
        out = bytearray()
        try:
            result = self._hs.write_message(payload, out)
        except Exception as e:  # pragma: no cover - defensive
            raise HandshakeError(f"write_message failed: {e}") from e
        if result is not None:
            # Final message; result is (CipherState, CipherState)
            self._set_transport_keys(result)
        else:
            self._capture_remote_static()
        return bytes(out)

    def read_handshake_message(self, message: bytes) -> bytes:
        """Consume an incoming handshake message and return any payload.

        On the third (final) message, transport keys are derived and
        ``handshake_finished`` flips True.

        Raises :class:`HandshakeError` on MAC failure, decryption failure, or
        protocol-state error (e.g. peer used the wrong static key).
        """
        if self._hs is None:
            raise HandshakeError("handshake already finished; use decrypt()")
        out = bytearray()
        try:
            result = self._hs.read_message(bytes(message), out)
        except Exception as e:
            raise HandshakeError(f"read_message failed: {e}") from e
        if result is not None:
            self._set_transport_keys(result)
        else:
            self._capture_remote_static()
        return bytes(out)

    # ---- Transport phase ----

    def encrypt(self, plaintext: bytes, ad: bytes = b"") -> bytes:
        if not self.handshake_finished:
            raise HandshakeError("handshake not finished; cannot encrypt yet")
        assert self._cs_send is not None
        return bytes(self._cs_send.encrypt_with_ad(ad, plaintext))

    def decrypt(self, ciphertext: bytes, ad: bytes = b"") -> bytes:
        if not self.handshake_finished:
            raise HandshakeError("handshake not finished; cannot decrypt yet")
        assert self._cs_recv is not None
        try:
            return bytes(self._cs_recv.decrypt_with_ad(ad, bytes(ciphertext)))
        except Exception as e:
            raise HandshakeError(f"decrypt_with_ad failed: {e}") from e


class XXInitiator(_XXBase):
    """Client side of the Noise XX handshake.

    The initiator may pass an *expected* remote static pubkey (a
    :class:`~tabula_wire.crypto.keys.PublicKey` or 32 raw bytes). XX learns
    the remote static during the handshake, not before, but the pinning layer
    wants to fail-fast if what arrives doesn't match what was
    out-of-band-trusted. We do that check at the moment the remote static
    becomes known — i.e. when the second handshake message has been read.
    """

    _initiator = True

    def __init__(
        self,
        local_static: SecretKey,
        remote_static_pubkey: Union[PublicKey, bytes, bytearray, None] = None,
    ) -> None:
        super().__init__(local_static)
        self._expected_remote_static = _coerce_pin(remote_static_pubkey)
        self._initialize()

    def read_handshake_message(self, message: bytes) -> bytes:
        out = super().read_handshake_message(message)
        # After reading message #2, the responder's static pubkey is known.
        # Enforce pin-match here so we fail BEFORE sending message #3 (which
        # would leak our static identity to a wrong peer).
        if (
            self._expected_remote_static is not None
            and self._remote_static_pubkey is not None
            and self._remote_static_pubkey != self._expected_remote_static
        ):
            expected_hex = self._expected_remote_static.hex()[:16]
            actual_hex = self._remote_static_pubkey.hex()[:16]
            raise HandshakeError(
                "remote static pubkey mismatch (pinning failure): "
                f"expected <{expected_hex}...>, got <{actual_hex}...>"
            )
        return out


class XXResponder(_XXBase):
    """Server side of the Noise XX handshake.

    Responder does not pre-pin the initiator's static (TOFU happens at the
    application layer once the handshake completes). The exposed
    ``remote_static_pubkey`` after ``handshake_finished`` is what the
    application uses for authn / authz decisions.
    """

    _initiator = False

    def __init__(self, local_static: SecretKey) -> None:
        super().__init__(local_static)
        self._initialize()
