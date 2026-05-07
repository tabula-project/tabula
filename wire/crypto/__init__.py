"""Noise transport wrapper for the wire layer.

The real Noise XX implementation lands in #18; in the meantime this
package exposes a stub responder with the same shape so the listener
in #20 can be written and tested end-to-end. The stub is documented as
NON-CRYPTOGRAPHIC and intended only to exercise the framing/state
machine plumbing in tests.

Integration point with #18:
    Replace ``XXResponderStub`` with ``wire.crypto.noise_xx.XXResponder``.
    The listener depends on the protocol defined in
    :class:`NoiseResponderProtocol` only — no implementation details
    of the stub leak into the listener.
"""

from .protocol import (
    HandshakeError,
    NoiseResponderProtocol,
    NoiseTransportProtocol,
)
from .stub import XXResponderStub

__all__ = [
    "HandshakeError",
    "NoiseResponderProtocol",
    "NoiseTransportProtocol",
    "XXResponderStub",
]
