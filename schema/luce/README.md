# Luce extension namespace

Schema extensions specific to Luce. These are not required of core
adopters (Bower, TWIN). Per `CONTRIBUTING.md`, a type or field belongs in
core when at least two consumers want it; otherwise it lives here.

## Layout

```
schema/luce/
├── README.md             — this file
└── v1/
    ├── types/            — wholly new types Luce uses (shot, sequence, task, ...)
    └── extensions/       — optional fields added to core types via allOf + $ref
        └── decision.yaml — adds conservation_class to core `decision`
```

The directory split is deliberate. A reader scanning `schema/luce/v1/`
sees at a glance which files define new types and which augment core
types — the two patterns have different validation implications and
different upgrade paths, so keeping them separate keeps surprises
minimal.

## Two extension patterns

### 1. New type in extension namespace

**Path:** `schema/luce/v1/types/<name>.yaml`

Wholly new content types Luce uses but core consumers don't. Examples
(planned, not all landed):

- `shot` — single film shot (Eve production)
- `sequence` — ordered group of shots
- `task` — production task assigned to a person/agent

Records of these types carry `type: luce:shot` (namespaced form) in
their frontmatter. Core validators that don't load Luce extensions
don't recognize the type and reject the record — that's fine, because
those records aren't meant for core consumers.

### 2. Field extension on core type

**Path:** `schema/luce/v1/extensions/<core-type>.yaml`

Adds Luce-only optional fields to a core type. Uses JSON Schema's
`allOf` + `$ref` to compose the core schema with extension fields:

```yaml
allOf:
  - $ref: ../../../v1/types/<core-type>.yaml
  - type: object
    properties:
      <luce-only-field>:
        type: ...
```

Validators that load the extension see core-required fields plus the
extension's optional fields. Validators that load only the core type
never see the extension fields.

**Note on permissive validation.** `frontmatter-base.yaml` and the core
type schemas do not currently set `additionalProperties: false`. Default
JSON Schema behavior is permissive: a core decision record carrying
`conservation_class: budget` validates against core (the field is
ignored, not rejected). This is convenient for incremental rollout but
means the extension fields are not strictly hidden from core validators
— they're just unmodeled there. Tightening to strict rejection is a
separate decision and would be tracked as its own issue.

## Validator implications

The reference validator (`tools/validate.py`, deferred) will need to
know which extension namespace(s) a record claims. A reasonable
convention:

- A record with `type: decision` validates against core `decision`.
- A record with `type: decision` that also carries Luce fields
  (`conservation_class`, etc.) should additionally validate against
  `schema/luce/v1/extensions/decision.yaml`.
- A record with `type: luce:shot` validates only against the Luce
  extension type.

The mechanism for declaring "this record uses these extensions" is open
— either inferred from field presence, set in a top-level
`extensions: [luce]` array, or driven by repository-level config. That
decision rides with the validator PR, not this one.

## Currently defined

| Path | Pattern | Adds |
|---|---|---|
| `v1/extensions/decision.yaml` | field extension | `conservation_class` (optional string) |

Planned (not yet landed):

- `v1/types/shot.yaml`, `v1/types/sequence.yaml`, `v1/types/task.yaml` — new types
- Additional field extensions as Luce surfaces them

## Adding to this namespace

- **New Luce-only type** → add `v1/types/<name>.yaml`. Make sure
  `frontmatter-base.yaml`'s `type` pattern accepts your namespaced form
  (`luce:<name>` matches `^([a-z_]+|[a-z_]+:[a-z_]+)$`).
- **New optional field on a core type** → add or extend
  `v1/extensions/<core-type>.yaml`. Compose via `allOf` + `$ref` to the
  core type. Keep added fields **optional** — promoting a field to
  required would force the core type to require it too, defeating the
  point of the extension.
- **Field needed by two or more consumers** → propose it for core
  instead. See `CONTRIBUTING.md`.
