# Tabula

**A sovereign-AI common core: privacy-class routing, cold-by-default compute, decadal substrate.**

> *Tabula: the durable surface beneath whatever you build.*

Tabula is open-source infrastructure shared by a small set of independent applications — TWIN, Luce, Bower, and others — that each need the same primitives: sovereign-class compute, durable typed memory, and a knowledge graph derived from both.

## Status (2026-05-07)

MVP code complete end-to-end for a Bower-shaped chat enclave: wire protocol (Noise XX + protobuf), CLI (`tabula enclave {up,down,status,ssh}` + `chat` + `keygen`), GCP Terraform modules, audit + cost guardrails, GPU bootstrap. Tested locally; not yet deployed.

**Next**: stand up a real GCP enclave; decide [#90](https://github.com/tabula-project/tabula/issues/90) (finish vs remove `etl/` + `l3/` for chat-session memory).

See [SPEC.md](SPEC.md) for full architecture and the [closed Epic #12](https://github.com/tabula-project/tabula/issues/12) / [Epic #13](https://github.com/tabula-project/tabula/issues/13) for the MVP shape.

## Layout

| Path | What |
|---|---|
| [`wire/`](wire/) | `tabula-wire` package — Noise XX, protobuf, server, client |
| [`cli/`](cli/) | `tabula-cli` package — the `tabula` console script |
| [`terraform/`](terraform/) | GCP modules + root enclave composition |
| [`bootstrap/`](bootstrap/) | VM startup scripts (GPU, classifier wake) |
| [`schema/`](schema/) | content-type vocabulary (v1 draft) |
| [`l0/`](l0/), [`l1/`](l1/), [`l3/`](l3/), [`etl/`](etl/) | layer specs (prose); `l0/router/` is vendored code |
| [`docs/`](docs/) | architecture, patterns, decisions |

## Governance

Three-org steward model: @omniscia (TWIN), Vivake / Good Studios (Luce), @rjwalters / 2AM Logic (Bower). See [CONTRIBUTING.md](CONTRIBUTING.md). Apache 2.0.
