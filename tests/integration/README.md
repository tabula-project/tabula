# Localhost end-to-end integration tests (issue #34)

This directory contains the gate test for **Epic #13 (chat MVP)**: a single-host
smoke test proving the wire stack composes correctly end-to-end.

## What's here

| File | What it does |
|---|---|
| `conftest.py` | Reusable harness: `SocketTee`, `ephemeral_port()`, `generate_x25519_keypair()`, wire-stack availability probe. |
| `test_harness.py` | Unit tests for the harness itself. **Always runs in CI.** |
| `test_e2e_localhost.py` | Async pytest test: client → Noise XX → server → claude subprocess → stream. |
| `test_cli_e2e.py` | Subprocess-driven CLI test: `tabula keygen` → `tabula servers add` → `tabula chat connect demo`. |
| `test_cli_e2e.sh` | Optional shell-script equivalent of the CLI test (manual demo walkthrough). |
| `fixtures/fake_claude.sh` | Deterministic `claude` stand-in used in CI. |

## Skip behavior

The e2e tests probe for the canonical `tabula_wire` layout from issue **#57**:

- `tabula_wire.framing`
- `tabula_wire.crypto.{noise,keys}`
- `tabula_wire.proto.v1`
- `tabula_wire.server.{listener,session}`
- `tabula_wire.client.{dialer,pinning}`

If any of these are missing they **skip with a clear diagnostic listing what
isn't importable yet**. This lets the harness keep building on every PR while
the dependency chain (#16, #18, #20, #22, #25, #27, #29, #31, #32) lands
piece by piece.

## Running locally

```bash
# Just the harness unit tests (no wire stack required):
pytest tests/integration/test_harness.py -v

# Full e2e suite once the wire stack is installed:
pip install -e wire -e cli
pytest tests/integration -v -ra

# Optional: drive against the real `claude` CLI (NEVER set in CI):
TABULA_E2E_REAL_CLAUDE=1 pytest tests/integration/test_e2e_localhost.py -k real_claude -s

# Shell-based CLI walkthrough (mirrors the user-facing demo):
./tests/integration/test_cli_e2e.sh
```

## What the e2e test actually asserts

1. A fresh client+server keypair is generated in a tmp dir.
2. The server is spawned in-process on an **ephemeral port** (port 0,
   resolved at runtime — never hardcoded).
3. A `SocketTee` is wedged between client and server so every byte in
   either direction is captured.
4. The client dialer establishes a Noise XX session, sends `Hello` /
   `UserMessage` with a unique prompt phrase, and consumes streamed
   `AssistantToken` frames until `AssistantTurnEnd`.
5. The fake-claude fixture's deterministic response phrase appears in the
   collected token stream.
6. **The unique prompt phrase and the response phrase do not appear in
   plaintext on the wire** — only ciphertext.

Plaintext leaks fail the test loudly. The fake-claude phrase is intentionally
unusual (`zephyr-quokka-prism-mango-vortex-glissando`) to make false-negatives
extremely unlikely.
