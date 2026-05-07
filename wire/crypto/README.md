# `wire/crypto/` — Noise XX wrappers

Issue: #18 (sub-issue of epic #13)

Pure Python wrapper around the [Noise Protocol Framework](https://noiseprotocol.org/)
that exposes a tiny initiator/responder API for the rest of Tabula's wire layer
to consume. **No network I/O lives here** — the wrapper is a state machine over
bytes; TCP and length-prefix framing live in sibling sub-issues
(#20 server, #27 client).

## Library choice — `dissononce`

We use [`dissononce`](https://github.com/tgalal/dissononce) (PyPI: `dissononce`).

| Why | Notes |
|---|---|
| Pure Python | No native build step; trivial to deploy in a stripped enclave VM. |
| Mature & widely used | Core of the [`python-noiseprotocol`](https://pypi.org/project/noiseprotocol/) ecosystem and several Signal-protocol re-implementations. |
| Permissive license | MIT. |
| Cipher suite parity | Supports the cipher suite we want out of the box: `Noise_XX_25519_ChaChaPoly_BLAKE2s`. |
| Active enough | Latest release is 0.34.3 (2023). The project has been stable; the underlying primitives come from `cryptography`, which IS actively maintained. |

Alternatives evaluated:

- [`noiseprotocol`](https://pypi.org/project/noiseprotocol/) — wraps `dissononce`. We import `dissononce` directly to skip the wrapper layer.
- `pynoise` — abandoned, last release 2018. Skip.

## Cipher suite

**Locked:** `Noise_XX_25519_ChaChaPoly_BLAKE2s`.

| Component | Choice | Why |
|---|---|---|
| Pattern | `XX` | Mutual authentication, forward secrecy, no PKI dependency. The handshake exposes both sides' static keys to each other so application-layer code can pin / authz on them. |
| DH | `25519` | X25519 — fast, well-supported, exclusive choice in modern protocols (TLS 1.3, WireGuard, Signal). |
| AEAD | `ChaChaPoly` | ChaCha20-Poly1305. Pure-Python performance-acceptable; constant-time without HW AES. |
| Hash | `BLAKE2s` | Fast in pure Python, 256-bit output, used by Wireguard. |

The epic (#13) does not lock the suite; this is the chosen default. Changing
the suite is a wire-format break; do it via RFC, not casually.

## Threat model assumptions (per epic #13, threat model (b))

- **Trust boundary:** end-to-end client process ↔ server process. The deployment
  target (GCP enclave) is *trusted*; the network path between is *not*.
- **Authentication:** mutual, via X25519 static keys generated per side.
  Server pubkey is pinned by client out-of-band (TOFU known_servers file —
  sub-issue #32). Client pubkey is presented to server's application layer
  (`remote_static_pubkey`) for authz decisions.
- **Confidentiality + integrity:** ChaCha20-Poly1305 AEAD on every transport
  message. Per-direction nonces are managed by `dissononce.CipherState` and
  advance monotonically — replay across a session is not possible.
- **Forward secrecy:** XX gives both-sides forward secrecy via ephemeral keys.
  Compromising a static private key does not decrypt past sessions.
- **What this does NOT cover:** denial of service, timing side channels in
  pure-Python primitives, key-storage at rest (handled by sub-issues
  #31 keygen, #32 pinning store), and traffic analysis (size/timing leakage).

## API at a glance

```python
from tabula_wire_crypto import (
    XXInitiator, XXResponder, HandshakeError,
    StaticKeyPair, generate_static_keypair,
)

# 1. Generate static keys per side (out-of-band trust)
client = generate_static_keypair()
server = generate_static_keypair()

# 2. Build state machines
init = XXInitiator(local_static=client, remote_static_pubkey=server.public)
resp = XXResponder(local_static=server)

# 3. Drive the 3-message XX handshake (caller does I/O)
m1 = init.write_handshake_message();  resp.read_handshake_message(m1)  # -> e
m2 = resp.write_handshake_message();  init.read_handshake_message(m2)  # <- e, ee, s, es
m3 = init.write_handshake_message();  resp.read_handshake_message(m3)  # -> s, se

assert init.handshake_finished and resp.handshake_finished

# 4. Pinning hook
assert init.remote_static_pubkey == server.public
# Server learns client's pubkey too — for application-layer authn:
authn_pubkey = resp.remote_static_pubkey

# 5. Transport phase — full-duplex
ct = init.encrypt(b"hello")
assert resp.decrypt(ct) == b"hello"

ct2 = resp.encrypt(b"world")
assert init.decrypt(ct2) == b"world"
```

`HandshakeError` is raised on:

- AEAD MAC failure (corrupted handshake message)
- Pin mismatch (initiator pinned a pubkey that doesn't match what the
  responder presented)
- Calling `encrypt`/`decrypt` before the handshake finishes
- Calling `*_handshake_message` after it finishes

## How to upgrade `dissononce`

Pinned in `pyproject.toml` to `>=0.34.3,<0.35` — patch updates flow, minor
versions do not. Before bumping the cap:

1. Read the upstream changelog. Note any changes to:
   - `HandshakeState.write_message` / `read_message` signatures
   - `CipherState.encrypt_with_ad` / `decrypt_with_ad` (ours expects
     `(ad, plaintext_or_ciphertext) -> bytes`)
   - `XXHandshakePattern` initialization (we pass `prologue=b""`, `s=keypair`)
2. Run `pytest wire/crypto/tests/ -v`. The smoke test has roundtrip + pin-
   mismatch + corruption coverage.
3. If 1.x is released, expect breaking API changes — not a casual bump.

## Files

| File | Purpose |
|---|---|
| `src/tabula_wire_crypto/__init__.py` | Public re-exports. |
| `src/tabula_wire_crypto/keys.py` | `StaticKeyPair`, `generate_static_keypair`, raw-bytes helpers. |
| `src/tabula_wire_crypto/noise_xx.py` | `XXInitiator`, `XXResponder`, `HandshakeError`. |
| `tests/test_noise_xx.py` | Smoke test from issue #18 acceptance criteria. |
| `pyproject.toml` | Package metadata + dependency pins. |

## Out of scope (separate sub-issues)

- TCP transport: #20 (server), #27 (client)
- Length-prefix framing: #20 (server), #27 (client)
- Keygen CLI: #31
- Pinning store / `known_servers` file: #32
