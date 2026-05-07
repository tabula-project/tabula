"""X25519 static-keypair helpers for the Noise XX wire layer.

This module is deliberately tiny — it wraps `dissononce`'s X25519 DH so the
public API hides the internal `dissononce.dh.x25519` types from callers. The
keygen CLI (sub-issue #31) builds on top of these primitives; the pinning
store (sub-issue #32) consumes the raw 32-byte public-key form.

Threat model note (per epic #13, threat model (b)):
- Static keys are held client-side and server-side; trust is bootstrapped
  out-of-band via TOFU pinning, not PKI.
- Key generation uses dissononce's X25519DH, which delegates to the
  cryptography library's CSPRNG. We do NOT roll our own RNG.
"""

from __future__ import annotations

from dataclasses import dataclass

from dissononce.dh.keypair import KeyPair as _DissKeyPair
from dissononce.dh.x25519.public import PublicKey as _DissPublicKey
from dissononce.dh.x25519.x25519 import X25519DH


# X25519 public/private keys are exactly 32 bytes per RFC 7748.
KEY_LEN = 32


@dataclass(frozen=True)
class StaticKeyPair:
    """An X25519 static keypair for one Noise endpoint.

    Holds the raw 32-byte public key alongside the underlying dissononce
    KeyPair object. The dissononce object is what `XXInitiator` /
    `XXResponder` need internally; the `public` bytes are what the pinning
    store and `tabula keygen` CLI need.

    NOTE: This class is treated as a SECRET. Do not log it, do not stringify
    it casually. The repr() is intentionally redacted for the private side.
    """

    # The 32-byte X25519 public key (safe to share, used for pinning).
    public: bytes

    # Internal dissononce object — used to feed the handshake state. Holds
    # the private scalar; never serialize this directly.
    _diss_keypair: _DissKeyPair

    def __post_init__(self) -> None:
        if not isinstance(self.public, bytes) or len(self.public) != KEY_LEN:
            raise ValueError(
                f"public key must be {KEY_LEN} bytes, got "
                f"{type(self.public).__name__} of length "
                f"{len(self.public) if isinstance(self.public, (bytes, bytearray)) else 'n/a'}"
            )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"StaticKeyPair(public=<{self.public.hex()[:16]}...>, private=<redacted>)"


def generate_static_keypair() -> StaticKeyPair:
    """Generate a fresh X25519 static keypair using dissononce's DH primitive.

    Internally calls `cryptography`'s X25519PrivateKey.generate(), which uses
    the OS CSPRNG.
    """
    dh = X25519DH()
    kp = dh.generate_keypair()
    return StaticKeyPair(public=bytes(kp.public.data), _diss_keypair=kp)


def public_key_from_bytes(raw: bytes) -> _DissPublicKey:
    """Reconstruct a dissononce X25519 PublicKey from raw 32 bytes.

    Used by the initiator to build a pinning expectation BEFORE the handshake:
    the caller has the server's pubkey from out-of-band trust (a known_servers
    file, sub-issue #32), and needs to hand a dissononce object to the wrapper.

    The wrapper does NOT pre-load this into the handshake state (XX learns the
    remote static during the handshake, not before). Instead, the wrapper
    keeps the expected pubkey aside and verifies it against the actually-seen
    pubkey after the handshake completes. See `XXInitiator.__init__`.
    """
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != KEY_LEN:
        raise ValueError(f"raw key must be {KEY_LEN} bytes, got {len(raw)}")
    return _DissPublicKey(bytes(raw))
