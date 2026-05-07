# L1 Substrate Invariant — Plain Text + Git

> Source: omniscia/twin TWIN-V1.md §3.1.

The corpus is markdown files with YAML frontmatter, stored in git repositories. Plain text + git is the most durable software stack we have — text is decades-stable, git's commit model is content-addressable and append-only by construction, and both are universally implementable.

## Properties

- **Every memory is a single markdown file.** One file = one entity.
- **Every change is a commit.** History is preserved indefinitely. No "edit-in-place" data loss.
- **Multi-machine sync is git push/pull.** No bespoke replication protocol.
- **Append-only by construction.** `supersedes` relations chain newer over older without losing the audit trail.

## Derived layers are replaceable

Indexes (Postgres, Apache AGE graph, pgvector, full-text) are *derived* from the corpus. They can be rebuilt on any future infrastructure given just the corpus.

**Why this matters for survival:** in 50 years, when current databases and tools are obsolete, the corpus will still be readable with `cat`. Every other layer is replaceable. The substrate outlives any single tool, framework, or vendor.

## What this constrains

- **No database-only writes.** Anything that needs to persist beyond a single tool must land in L1 markdown. The graph (L3) and operational logs (L2) are caches, not sources of truth.
- **Frontmatter is the API.** Tools query and join via frontmatter fields, not by parsing markdown bodies.
- **Bodies can be encrypted.** Frontmatter stays plaintext for queryability (see [`encryption.md`](encryption.md)).

## What this enables

- **Tool independence.** Any markdown editor + git client = full read/write access. The substrate works without Tabula's own tooling.
- **Forensic auditability.** `git log` + `git blame` over decades.
- **Deterministic rebuild.** Clone the corpus on a fresh machine; rebuild Postgres + AGE + pgvector indexes from scratch in minutes.
