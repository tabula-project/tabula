# tabula-wire

Transport layer for Tabula: Noise-XX wire protocol, static-key tooling, and the
top-level `tabula` CLI.

## Status

Bootstrapping. The first deliverable is the **static-key tooling** required by
both client and server for the Noise XX handshake (see issue #31):

- `tabula keygen` — generate an X25519 keypair, store the secret at `0o600`,
  print the public key in stable hex.
- `tabula_wire.crypto.keys` — canonical loader/saver for the
  on-disk secret-key format. The Noise XX wrapper (#18) and the dialer (#27)
  consume this loader.

Future scope (separate sub-issues, not landed here):

- `wire/crypto/noise_xx.py` — Noise XX wrapper around the chosen library (#18).
- `wire/transport/` — TCP transport + length-prefixed framing.
- `cli/chat.py` — `tabula chat connect` subcommand (#29).
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
