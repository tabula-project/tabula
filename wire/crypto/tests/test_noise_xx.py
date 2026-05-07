"""Smoke tests for the Noise XX wrapper (issue #18 acceptance criteria).

These tests are pure in-memory — no sockets, no async. They verify:
    1. A full XX handshake completes between an initiator and a responder
       running off the same wrapper API.
    2. After handshake, ciphertext encrypted by either side decrypts on the
       other (transport keys agree in both directions).
    3. The initiator sees the responder's static pubkey it was given
       out-of-band (pinning hook).
    4. The handshake fails cleanly when the initiator was given a WRONG
       expected remote static pubkey.

The 3-message XX exchange is driven by the test directly so any wrong-order
or bad-byte injection is easy to add later.
"""

from __future__ import annotations

import pytest

from tabula_wire_crypto import (
    HandshakeError,
    StaticKeyPair,
    XXInitiator,
    XXResponder,
    generate_static_keypair,
)


# ----- helpers ---------------------------------------------------------------

def _run_handshake(init: XXInitiator, resp: XXResponder) -> None:
    """Drive the 3-message XX exchange to completion, in-memory."""
    # Message 1: -> e
    msg1 = init.write_handshake_message()
    resp.read_handshake_message(msg1)
    # Message 2: <- e, ee, s, es
    msg2 = resp.write_handshake_message()
    init.read_handshake_message(msg2)
    # Message 3: -> s, se
    msg3 = init.write_handshake_message()
    resp.read_handshake_message(msg3)


# ----- tests -----------------------------------------------------------------

def test_full_handshake_and_transport_roundtrip() -> None:
    """End-to-end: handshake completes, both directions encrypt/decrypt."""
    init_static = generate_static_keypair()
    resp_static = generate_static_keypair()

    init = XXInitiator(
        local_static=init_static,
        remote_static_pubkey=resp_static.public,
    )
    resp = XXResponder(local_static=resp_static)

    assert not init.handshake_finished
    assert not resp.handshake_finished

    _run_handshake(init, resp)

    assert init.handshake_finished
    assert resp.handshake_finished

    # Round-trip plaintext both directions.
    pt_a = b"client says hello"
    ct_a = init.encrypt(pt_a)
    assert ct_a != pt_a  # Sanity: ChaChaPoly produced ciphertext + tag.
    assert resp.decrypt(ct_a) == pt_a

    pt_b = b"server replies world"
    ct_b = resp.encrypt(pt_b)
    assert init.decrypt(ct_b) == pt_b


def test_initiator_sees_expected_responder_static() -> None:
    """After handshake, initiator's view of remote pubkey == responder's actual pubkey."""
    init_static = generate_static_keypair()
    resp_static = generate_static_keypair()

    init = XXInitiator(
        local_static=init_static,
        remote_static_pubkey=resp_static.public,
    )
    resp = XXResponder(local_static=resp_static)

    _run_handshake(init, resp)

    assert init.remote_static_pubkey == resp_static.public
    # And the responder learns the initiator's pubkey too (for app-layer authn).
    assert resp.remote_static_pubkey == init_static.public


def test_handshake_fails_with_wrong_expected_remote_static() -> None:
    """If the initiator pins a different pubkey than the responder presents,
    the handshake aborts before message 3 with HandshakeError."""
    init_static = generate_static_keypair()
    resp_static = generate_static_keypair()
    # Wrong pin: a third unrelated keypair's public bytes
    decoy = generate_static_keypair()

    init = XXInitiator(
        local_static=init_static,
        remote_static_pubkey=decoy.public,  # wrong pin
    )
    resp = XXResponder(local_static=resp_static)

    msg1 = init.write_handshake_message()
    resp.read_handshake_message(msg1)
    msg2 = resp.write_handshake_message()

    # Reading message 2 should reveal the responder's actual static, which
    # mismatches the pin -> HandshakeError.
    with pytest.raises(HandshakeError, match="pinning failure"):
        init.read_handshake_message(msg2)

    # Initiator must NOT be in transport mode: we abort before message 3.
    assert not init.handshake_finished


def test_handshake_fails_on_corrupted_message() -> None:
    """Tampering with a handshake message MUST cause HandshakeError, not a silent pass.

    This exercises the AEAD MAC failure path through the wrapper.
    """
    init_static = generate_static_keypair()
    resp_static = generate_static_keypair()

    init = XXInitiator(
        local_static=init_static,
        remote_static_pubkey=resp_static.public,
    )
    resp = XXResponder(local_static=resp_static)

    msg1 = init.write_handshake_message()
    resp.read_handshake_message(msg1)
    msg2 = bytearray(resp.write_handshake_message())
    # Flip a byte in the encrypted-static portion of msg2 (after the 32-byte
    # ephemeral header). Picking offset ~40 hits the encrypted static block.
    msg2[40] ^= 0x01

    with pytest.raises(HandshakeError):
        init.read_handshake_message(bytes(msg2))


def test_encrypt_before_handshake_finished_raises() -> None:
    """Calling encrypt() before the handshake completes is a programming error."""
    init = XXInitiator(
        local_static=generate_static_keypair(),
        remote_static_pubkey=generate_static_keypair().public,
    )
    with pytest.raises(HandshakeError, match="handshake not finished"):
        init.encrypt(b"too early")


def test_static_keypair_validates_public_length() -> None:
    """Construction safety: only accept 32-byte X25519 public keys."""
    real = generate_static_keypair()
    with pytest.raises(ValueError):
        # private side stays valid; public side is wrong length.
        StaticKeyPair(public=b"\x00" * 16, _diss_keypair=real._diss_keypair)


def test_initiator_rejects_wrong_length_pin() -> None:
    """A pin of the wrong length is a programming error, not a handshake error."""
    init_static = generate_static_keypair()
    with pytest.raises(ValueError):
        XXInitiator(local_static=init_static, remote_static_pubkey=b"\x00" * 16)
