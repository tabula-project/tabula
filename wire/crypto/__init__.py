"""Stub Noise XX wrapper pending the real implementation in #18.

This module gives #27 (the client dialer) a stable, byte-format-equivalent
``XXInitiator`` / ``XXResponder`` API to build against. Once #18 lands, the
real ``wire.crypto.noise_xx`` module will replace this stub and callers that
``from wire.crypto import XXInitiator`` will continue to work unchanged.

Cipher suite (per #18 implementation notes):
``Noise_XX_25519_ChaChaPoly_BLAKE2s``.
"""

from __future__ import annotations

from .noise_xx import (
    KeyMismatchError,
    KeyPair,
    NoiseError,
    XXInitiator,
    XXResponder,
    generate_keypair,
)

__all__ = [
    "KeyMismatchError",
    "KeyPair",
    "NoiseError",
    "XXInitiator",
    "XXResponder",
    "generate_keypair",
]
