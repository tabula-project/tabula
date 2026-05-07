"""Protocols (typing.Protocol) for the Noise transport responder.

These are the *contract* between the wire/server listener and whatever
Noise XX implementation we ship. The real implementation lives in
:mod:`wire.crypto.noise_xx` (issue #18). The stub in
:mod:`wire.crypto.stub` exists so #20 can be written and tested
independently.

Both implementations must satisfy ``NoiseResponderProtocol``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class HandshakeError(Exception):
    """Handshake failed (bad message, bad key, peer disconnect)."""


@runtime_checkable
class NoiseTransportProtocol(Protocol):
    """Post-handshake encryption/decryption interface."""

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt one transport message and return ciphertext."""
        ...

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt one transport message and return plaintext.

        Raises:
            HandshakeError: AEAD authentication failed (tampered
                ciphertext or out-of-order frame). The listener treats
                this as a fatal connection error.
        """
        ...

    @property
    def remote_static_pubkey(self) -> bytes:
        """The peer's static public key (for the pinning layer)."""
        ...


@runtime_checkable
class NoiseResponderProtocol(Protocol):
    """XX responder state machine.

    Usage by the listener::

        responder = make_responder(static_key)
        # message 1: <- e
        msg1 = await read_frame(reader)
        responder.read_handshake_message(msg1)
        # message 2: -> e, ee, s, es
        msg2 = responder.write_handshake_message(b"")
        await write_frame(writer, msg2)
        # message 3: <- s, se
        msg3 = await read_frame(reader)
        responder.read_handshake_message(msg3)
        assert responder.handshake_finished
        transport = responder.into_transport()
    """

    @property
    def handshake_finished(self) -> bool:
        """True once all three XX messages have been processed."""
        ...

    def read_handshake_message(self, ciphertext: bytes) -> bytes:
        """Consume one inbound handshake message; return inner payload.

        Raises:
            HandshakeError: malformed message, decryption failed, or
                called after handshake completion.
        """
        ...

    def write_handshake_message(self, payload: bytes) -> bytes:
        """Produce one outbound handshake message.

        Raises:
            HandshakeError: called when not the responder's turn or
                after handshake completion.
        """
        ...

    def into_transport(self) -> NoiseTransportProtocol:
        """Finalize handshake and return the transport-phase object.

        Raises:
            HandshakeError: handshake is not yet complete.
        """
        ...
