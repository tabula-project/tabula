# tabula-wire-server

Server-side components of the Tabula wire protocol (see [Epic #13]).

## Modules

- `claude_driver.py` — async subprocess driver around the `claude` CLI.
  Spawns one subprocess per chat session, pipes prompts in via JSONL on
  stdin, streams response tokens out via JSONL on stdout. Multi-turn over a
  single subprocess is supported (`claude --input-format stream-json
  --output-format stream-json`).

Other modules (TCP listener + Noise responder, session manager, frame
loop) are added by sibling sub-issues of #13.

## Running tests

```bash
cd wire/server
pip install -e ".[test]"
pytest
```

The tests use a bash fixture (`tests/fixtures/fake_claude.sh`) as a stand-in
for the real `claude` CLI, so no Vertex AI auth or network access is needed.

## Auth (production)

`claude_driver` does not configure auth. The session manager / deployment
must set, in the inherited environment:

- `CLAUDE_CODE_USE_VERTEX=1`
- `ANTHROPIC_VERTEX_PROJECT_ID=<gcp-project>`
- `CLOUD_ML_REGION=<region>` (e.g. `us-east5`)
- ADC via `GOOGLE_APPLICATION_CREDENTIALS` or VM service account.

[Epic #13]: https://github.com/tabula-project/tabula/issues/13
