# Contributing to Tabula

Tabula is governed by a small set of stewards from the founding consumer projects. Substantive changes go through a lightweight RFC process; routine work happens via pull requests.

## Stewards

The current steward set is in [README.md](README.md). Each steward has commit access on the substrate and represents one of the founding consumer projects.

## Decisions

- **Routine PRs** (bug fixes, doc improvements, schema typo fixes): reviewed and merged by any steward.
- **Substantive changes** (schema additions, new layer surface area, governance changes): lazy consensus among all stewards. Open an issue tagged `rfc`; if no objection within 7 days, proceed.
- **Breaking changes**: explicit approval from each steward and a deprecation cycle in the affected schema version.

## Schema vocabulary

Adding a content type to core:
1. Open an issue describing the type, motivating use case, and proposed frontmatter shape.
2. At least two consumers must want it for it to land in core; otherwise it lives in an extension namespace (`schema/luce/`, `schema/bower/`, …).
3. v2 schema work begins when at least two consumers ask for breaking changes against v1.

## Privacy-class invariant

The privacy-class router (see [SPEC.md § Privacy classes](SPEC.md)) is the substrate's strongest claim and its load-bearing one. Changes to routing rules require:

- Integration tests demonstrating the invariant holds for every backend
- Steward consensus (no lazy approval — explicit acks)
- An entry in the audit log explaining the change

## License of contributions

By submitting a pull request you agree to license your contribution under [Apache 2.0](LICENSE).
