# L1 Frontmatter Specification (v1)

> Source: omniscia/twin architecture/TWIN-V1.md Appendix A. Tabula is the canonical home; TWIN, Luce, and Bower are downstream consumers.

Every L1 entity (markdown file in git) carries YAML frontmatter. The body follows, possibly encrypted. Frontmatter stays plaintext for queryability.

## Required fields

```yaml
id: 01HYABCD5K2P7Q9X3Z8R4N6F2T          # ULID — sortable, collision-safe, human-finger-friendly
author: omniscia                          # canonical entity ID
audience: [org-maj]                       # one or more tier IDs (see encryption.md)
classification: org-internal                # human-readable label
source: telegram://botho-coord/msg-12345  # provenance URL / trace
```

## Timestamps

```yaml
created_at: 2026-05-03T04:55Z
modified_at: 2026-05-03T04:55Z            # null if append-only-no-mods
```

## Encryption

```yaml
encryption: age:v1:keyset-2026            # null for public tier; non-null → body is age-encrypted
```

## Entity references

```yaml
entities:                                 # canonical entity IDs this memory references
  - person:rjwalters
  - org:maj-foundation
  - decision:01HYABCD5K2P7Q9X3Z8R4N6F2T
```

## Relations

```yaml
relations:
  references: [01HXAAA, 01HYBBB]        # other memory IDs
  supersedes: []                          # this memory replaces these
  caused-by: []                           # causal predecessors
  is-about: [decision]                    # topic tags as entity references
```

## Categorical metadata

```yaml
tags: [identity, history-rewrite]       # free-form topic tags
release_trigger: immediate              # immediate | conditional | delayed | posthumous
release_condition: null                 # for conditional/delayed: e.g., "child_age >= 18"
redactions: []                           # field paths stripped from non-privileged views
external_ids: {}                         # kitsu_uuid, github_sha, etc.
```

## Schema enforcement

Each content type in `schema/v1/` has a JSON Schema that validates the frontmatter subset relevant to that type. The base schema (`schema/v1/frontmatter-base.yaml`) validates the universal fields above; type schemas extend it with type-specific optional fields.

## Lineage over time

`modified_at` + `supersedes` relations form an append-only chain. The canonical version is the newest `modified_at` among a chain; older versions remain in git history for audit. `append-only-no-mods` files set `modified_at: null` and never supersedes — they accumulate (decision traces, observations).
