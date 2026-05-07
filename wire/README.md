# wire — Tabula chat transport (stubs in this PR)

This directory will eventually hold:

- `wire/proto/` — protobuf schema + generated bindings (#16)
- `wire/client/` — TCP dialer + Noise XX initiator + framed loop (#27)
- `wire/server/` — TCP listener + Noise XX responder (#20)
- `wire/framing.py` — shared length-prefix codec
- `wire/crypto/` — vendored Noise (#18)

PR #29 (chat UI) ships **stub implementations** of the minimum surface the
UI consumes: frame dataclasses (`wire.proto`), a `ChatChannel` protocol +
`connect()` placeholder (`wire.client.dialer`), a typed exception hierarchy
(`wire.client.exceptions`), and a no-op pinning lookup
(`wire.client.pinning`). When the dependent issues merge, those stubs
should be replaced; the UI's import paths are stable.

The chat UI tests inject a fake `ChatChannel` and never call the stub
`connect()`, so swapping the stubs for the real implementations does not
require touching `cli/`.
