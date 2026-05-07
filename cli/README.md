# tabula CLI

User-facing Tabula command-line tools.

```
tabula chat connect <server-name>     # open an encrypted chat session
tabula chat connect --host H --port P --accept-key HEX
tabula keygen                         # stub — see #31
tabula servers ...                    # stub — see #32
```

This package owns the top-level `tabula` console script entrypoint
(`cli:main`) and the `chat connect` subcommand (issue #29).

## Module layout

- `cli/__init__.py` — top-level argparse entrypoint and subcommand registry
- `cli/__main__.py` — dev shim so `python -m cli ...` works pre-install
- `cli/chat.py` — `chat connect`: readline UI + streaming render
- `cli/tests/test_chat_ui.py` — unit + smoke tests

The chat UI talks to the enclave through `wire.client` (a sibling package
in this repo). Until #16 / #27 / #31 / #32 land, `wire/` ships minimal
stubs so this CLI can be built and tested in isolation.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Clean exit (EOF / EndSession ack) |
| 1 | Generic failure |
| 2 | Usage error (argparse, missing key, unpinned label without overrides) |
| 3 | Server `ErrorFrame` received |
| 4 | Server disconnected mid-session |
| 5 | Handshake / pinning / protocol failure |
| 130 | Ctrl-C |

## Running tests

```
cd cli
pytest -v
```

The conftest adds the worktree root to `sys.path`, so no editable install
is required for the inner-loop test workflow.

## Platform support

macOS and Linux for MVP. Windows is unsupported per issue #29 scope notes;
the readline-thread import is wrapped in `try/except ImportError` so the
module loads on Windows but interactive editing falls back to plain
`input()`.
