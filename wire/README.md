# tabula-wire

Transport layer for Tabula: Noise-XX wire protocol, static-key tooling, the
`tabula.wire.v1` protobuf schema, and the top-level `tabula` CLI.

> Parent epic: [#13 — E2E encrypted chat (Noise + protobuf)](https://github.com/tabula-project/tabula/issues/13)
> Layout: canonical `wire/` layout from [#57](https://github.com/tabula-project/tabula/issues/57).

## Status

Bootstrapping. Three deliverables have landed so far:

- **Static-key tooling** (#46): `tabula keygen` + `tabula_wire.crypto.keys`
  canonical loader/saver, required by both client and server for the Noise XX
  handshake (issue #31).
- **Typed exception hierarchy** (#36): `tabula_wire.errors` defines the
  `WireError` family (`HandshakeError`, `SessionError`, `SubprocessError`) plus
  cross-cutting `AtCapacity` / `InternalError`, and
  `exception_to_error_code()` maps exception types to `ErrorFrame.Code`.
- **`tabula.wire.v1` protobuf schema** (#16): generated bindings shipped at
  `tabula_wire.proto.v1`, consumed by the responder (#20) and dialer (#27).

## Layout

This is a single Python distribution, **`tabula-wire`**, src-layout:

```
wire/
  pyproject.toml                # tabula-wire distribution
  README.md                     # this file
  proto/
    chat.proto                  # schema source (flat, package tabula.wire.v1)
    build.sh                    # regen entry point; writes into src/tabula_wire/proto/v1
    README.md
  src/
    tabula_wire/
      __init__.py
      crypto/                   # static-key tooling (#46); Noise XX wrappers pending (#18)
      proto/
        __init__.py
        v1/                     # generated protobuf bindings (#16)
          __init__.py
          chat_pb2.py
          chat_pb2.pyi
      server/                   # responder side (pending #20, #25)
      client/                   # initiator side (pending #27, #32)
  tests/
    proto/
      test_roundtrip.py         # #16 round-trip tests
```

| Subpackage | Purpose | Status |
|---|---|---|
| `tabula_wire.crypto.keys` | Static-key file format + loader/saver | Implemented (#46) |
| `tabula_wire.proto.v1` | `tabula.wire.v1` schema + generated Python bindings | Implemented (#16) |
| `tabula_wire.crypto` (Noise) | Vendored Noise XX implementation | Pending (#18) |
| `tabula_wire.server` | TCP listener + Noise responder + frame loop + claude subprocess driver | Pending (#20, #22, #25) |
| `tabula_wire.client` | TCP dialer + Noise initiator + frame loop + terminal UI | Pending (#27, #32) |

Future scope (separate sub-issues, not landed here):

- `tabula_wire.crypto.noise_xx` — Noise XX wrapper around the chosen library (#18).
- `tabula_wire.server` / `tabula_wire.client` — TCP transport + length-prefixed framing (#55).
- `tabula chat connect` subcommand (#29).
- Pinning store (`tabula pin add/list/remove`, #32).

## Key file format (canonical, v1)

The `tabula keygen` writer and the `tabula_wire.crypto.keys` loader agree on
this exact format. Any other module that needs to read a Tabula static secret
key MUST use `tabula_wire.crypto.keys.load_secret_key`.

```
tabula-x25519-secret-v1
<64-character lowercase hex of the 32-byte X25519 raw secret scalar>
```

Rules:

- Two lines, UTF-8, terminated by `\n`. A trailing newline after the hex line
  is required.
- Magic header `tabula-x25519-secret-v1` is matched verbatim.
- Hex is lowercase, exactly 64 characters, decoding to 32 bytes.
- File permissions MUST be `0o600` on POSIX. Loader refuses files with broader
  bits set; writer enforces `0o600` via `os.umask` + `os.chmod`.
- No trailing whitespace, no comment lines, no PEM headers. Keep it boring.

Public keys are not stored on disk in this format; they are derived on demand
from the secret. The CLI prints them in lowercase hex (64 chars) for sharing
and pinning.

### Why hex, not base64?

Hex avoids padding and url-safe variants; copy-paste between humans, configs,
and pinning tables stays unambiguous. The 32 bytes fit on one line either way.

### Future: passphrase-encrypted keys

Out of scope for v1. Plain `0o600` files only. A future `v2` magic header will
indicate an encrypted variant; loader will dispatch on the magic line.

## Defaults

| Role | Default secret-key path |
|------|--------------------------|
| client | `~/.config/tabula/client_key` |
| server | `~/.config/tabula/server_key` (system-service deployments may prefer `/etc/tabula/server_key` and pass `--out` explicitly) |

The default config directory is created with mode `0o700` if missing.

## Threat model assumptions

- The host filesystem is trusted. An attacker with read access to a `0o600`
  file already has secrets-equivalent access to the user's account; we do not
  attempt to defend against that here.
- Process memory is not hardened. Keys live in Python `bytes`; do not rely on
  this for high-assurance settings.
- The keygen CLI is intended for operators provisioning a host once. It is not
  a hot-path tool.

## Testing

```
cd wire
pip install -e .[test]
pytest
```
