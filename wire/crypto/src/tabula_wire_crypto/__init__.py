"""tabula-wire-crypto — Noise Protocol Framework wrappers for Tabula's wire layer.

Public API:

    from tabula_wire_crypto import (
        XXInitiator,
        XXResponder,
        HandshakeError,
        generate_static_keypair,
        StaticKeyPair,
    )

    # 1. Generate per-side static keys (out-of-band trust establishment)
    server_kp = generate_static_keypair()
    client_kp = generate_static_keypair()

    # 2. Construct an initiator (client) and responder (server)
    init = XXInitiator(local_static=client_kp, remote_static_pubkey=server_kp.public)
    resp = XXResponder(local_static=server_kp)

    # 3. Drive the 3-message XX handshake (caller does I/O)
    msg1 = init.write_handshake_message()        # -> e
    resp.read_handshake_message(msg1)
    msg2 = resp.write_handshake_message()        # <- e, ee, s, es
    init.read_handshake_message(msg2)
    msg3 = init.write_handshake_message()        # -> s, se
    resp.read_handshake_message(msg3)

    assert init.handshake_finished and resp.handshake_finished

    # 4. Pinning hook: initiator now sees what static pubkey the responder presented
    assert init.remote_static_pubkey == server_kp.public

    # 5. Transport phase
    ct = init.encrypt(b"hello")
    pt = resp.decrypt(ct)

The wrappers do NO network I/O. They are state machines over bytes — TCP and
length-prefix framing live in sibling sub-issues (#20 server, #27 client).

Cipher suite (locked, see README): Noise_XX_25519_ChaChaPoly_BLAKE2s.
"""

from tabula_wire_crypto.keys import (
    StaticKeyPair,
    generate_static_keypair,
    public_key_from_bytes,
)
from tabula_wire_crypto.noise_xx import (
    HandshakeError,
    XXInitiator,
    XXResponder,
)

__version__ = "0.1.0"

__all__ = [
    "StaticKeyPair",
    "generate_static_keypair",
    "public_key_from_bytes",
    "XXInitiator",
    "XXResponder",
    "HandshakeError",
]
