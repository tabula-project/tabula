"""Regression tests for `schema/v1/types/*.yaml` (issue #86).

The original type files contained two `properties:` keys at the same level
under `allOf[1]` — one holding the load-bearing `type: { const: <typename> }`
constraint, the other holding the rest of the type's fields. PyYAML's default
`safe_load` silently dropped the first block (last-write-wins on duplicate
mapping keys), so:

  * The type-discriminator constraint was lost on load.
  * A record with `type: "shoebox"` would validate against decision.yaml.

Caveat about issue #87
======================
Seven of the nine type files contain unquoted regex patterns that parse as
flow mappings under PyYAML — e.g. ``items: {type: string, pattern: ^...$}``.
That is a separate bug tracked in issue #87 (regex-quantifier / flow-mapping
quoting) and is *out of scope* for issue #86.

So this test module verifies the #86 fix in two ways:

  A. **Text-level duplicate-key check** (works on every file regardless of
     #87): for each type file, scan the top-level `allOf[1]` block and
     assert there is exactly one `properties:` key at that indentation
     level. This is the direct regression guard for #86.

  B. **Loader-level checks** (parametrized over the files that already
     parse cleanly under PyYAML — currently `decision.yaml` and
     `person.yaml`): assert strict-load succeeds (no duplicate keys), the
     `type: { const: <typename> }` constraint survives the load, mismatched
     `type:` values are rejected by `jsonschema`, and matching values pass.
     Once #87 lands, the parametrization will widen to cover all nine
     files automatically.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


REPO_ROOT = Path(__file__).resolve().parents[2]
TYPES_DIR = REPO_ROOT / "schema" / "v1" / "types"

# Map filename stem -> the const value its `type:` discriminator must enforce.
EXPECTED_TYPE_CONST: dict[str, str] = {
    "conversation": "conversation",
    "decision": "decision",
    "event": "event",
    "observation": "observation",
    "person": "person",
    "place": "place",
    "project": "project",
    "tool": "tool",
    "vision": "vision",
}


# ---------------------------------------------------------------------------
# Strict YAML loader
# ---------------------------------------------------------------------------


class DuplicateKeyError(ValueError):
    """Raised by ``StrictSafeLoader`` when a YAML mapping has duplicate keys."""


class StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys.

    PyYAML's default behavior is last-write-wins on duplicate keys; this
    loader matches the strictness of ``ruamel.yaml.YAML(typ='safe')`` with
    ``allow_duplicate_keys=False`` without adding a new dependency.
    """


def _strict_construct_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(
            None,
            None,
            f"expected a mapping node, got {node.id}",
            node.start_mark,
        )
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(
                f"duplicate key {key!r} found in mapping at {key_node.start_mark}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictSafeLoader.add_constructor(  # type: ignore[no-untyped-call]
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _strict_construct_mapping,
)


# ---------------------------------------------------------------------------
# File discovery helpers
# ---------------------------------------------------------------------------


def _list_type_files() -> list[Path]:
    files = sorted(TYPES_DIR.glob("*.yaml"))
    assert files, f"no type files found under {TYPES_DIR}"
    return files


def _files_that_parse_under_pyyaml() -> list[Path]:
    """Return the subset of type files that currently parse under PyYAML.

    Excludes files that hit the issue #87 flow-mapping/regex-quantifier bug.
    The list is computed at collection time, so as soon as #87 is fixed
    every file becomes eligible without test changes.
    """
    parseable: list[Path] = []
    for f in _list_type_files():
        try:
            with f.open("r", encoding="utf-8") as fh:
                yaml.safe_load(fh)
        except yaml.YAMLError:
            continue
        parseable.append(f)
    return parseable


# ---------------------------------------------------------------------------
# Text-level guard (works on every file regardless of #87)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type_file", _list_type_files(), ids=lambda p: p.name)
def test_no_duplicate_top_level_properties_under_allof(type_file: Path) -> None:
    """The `allOf[1].properties:` key appears at most once.

    This is the direct regression guard for issue #86. It is text-level so it
    runs even on files blocked from full YAML parse by issue #87.

    The structural pattern we audit is:

        allOf:
          - $ref: ...
          - type: object
            required: [...]      # may also be 4-space indented
            properties:          # <-- exactly one of these
              ...

    We count occurrences of `^    properties:` (four-space indent) anywhere
    after `allOf:`. The pre-fix files had two; the fixed files have one.
    """
    text = type_file.read_text(encoding="utf-8")

    # Find the `allOf:` block; everything before is irrelevant.
    allof_idx = text.find("\nallOf:")
    assert allof_idx != -1, f"{type_file.name}: no top-level `allOf:` found"
    allof_text = text[allof_idx:]

    # Stop at the next top-level key (e.g. `examples:`) so we don't pick up
    # `properties:` blocks that legitimately live inside the examples doc.
    next_top_level = re.search(r"\n([a-zA-Z_]\w*):", allof_text[len("\nallOf:"):])
    if next_top_level is not None:
        allof_text = allof_text[: len("\nallOf:") + next_top_level.start()]

    # 4-space indentation = direct child of an `allOf:` member.
    matches = re.findall(r"^    properties:\s*$", allof_text, flags=re.MULTILINE)
    assert len(matches) == 1, (
        f"{type_file.name}: expected exactly one `    properties:` line "
        f"directly under an `allOf` member, found {len(matches)}. "
        f"Issue #86 regression — the duplicate `properties:` blocks must be "
        f"merged so the `type: const: <typename>` constraint is preserved."
    )


# ---------------------------------------------------------------------------
# Loader-level guards (run on parseable files; full set once #87 lands)
# ---------------------------------------------------------------------------

_PARSEABLE = _files_that_parse_under_pyyaml()


@pytest.mark.parametrize("type_file", _PARSEABLE, ids=lambda p: p.name)
def test_type_file_loads_under_strict_yaml(type_file: Path) -> None:
    """Each parseable type file loads cleanly under a duplicate-key-rejecting loader.

    Before the #86 fix, every type file raised ``DuplicateKeyError`` here
    because of the two `properties:` blocks under `allOf[1]`.
    """
    with type_file.open("r", encoding="utf-8") as fh:
        yaml.load(fh, Loader=StrictSafeLoader)  # should not raise


@pytest.mark.parametrize("type_file", _PARSEABLE, ids=lambda p: p.name)
def test_type_const_constraint_present(type_file: Path) -> None:
    """The `type: { const: <name> }` constraint survives the YAML load.

    Before the fix, the second `properties:` block silently overwrote the
    first, so `allOf[1].properties` had no `type` key. This test asserts
    the constraint is structurally present after parsing.
    """
    with type_file.open("r", encoding="utf-8") as fh:
        schema = yaml.safe_load(fh)

    expected = EXPECTED_TYPE_CONST[type_file.stem]
    all_of = schema.get("allOf")
    assert isinstance(all_of, list) and len(all_of) >= 2, (
        f"{type_file.name}: expected `allOf` with >=2 members"
    )
    type_obj_member = all_of[1]
    properties = type_obj_member.get("properties", {})
    assert "type" in properties, (
        f"{type_file.name}: `allOf[1].properties.type` missing — the "
        f"discriminator constraint was dropped on load (issue #86 regression)."
    )
    assert properties["type"].get("const") == expected, (
        f"{type_file.name}: expected `type.const == {expected!r}`, "
        f"got {properties['type'].get('const')!r}"
    )


def _resolved_schema_for_validation(type_file: Path) -> dict[str, Any]:
    """Build a flat JSON Schema that captures the type discriminator.

    The full schema uses ``$ref: ../frontmatter-base.yaml`` which would
    require a registry to resolve. For the purpose of asserting the
    ``const`` constraint is enforced, we collapse ``allOf`` into a plain
    object schema that:

      * inherits the second member's ``properties`` and ``required`` lists;
      * declares the discriminator field via ``type: { const: <name> }``.

    Sufficient to demonstrate that records with a wrong ``type:`` are
    rejected and records with the correct ``type:`` are accepted.
    """
    with type_file.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    type_member = raw["allOf"][1]
    flat: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": copy.deepcopy(type_member.get("properties", {})),
    }
    if "required" in type_member:
        flat["required"] = list(type_member["required"])
    return flat


@pytest.mark.parametrize("type_file", _PARSEABLE, ids=lambda p: p.name)
def test_example_validates_with_correct_type(type_file: Path) -> None:
    """Positive control: ``examples[0]`` validates against the (collapsed) schema."""
    with type_file.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    examples = raw.get("examples") or []
    assert examples, f"{type_file.name}: expected at least one example"
    record = copy.deepcopy(examples[0])

    schema = _resolved_schema_for_validation(type_file)
    validator = Draft202012Validator(schema)
    # We only care that the discriminator and other declared fields don't
    # reject this record. Skip pattern-related errors that come from
    # issue #87 (regex-quantifier bug); they're orthogonal to the #86 fix.
    errors = [
        e for e in validator.iter_errors(record)
        if e.validator != "pattern"
    ]
    assert not errors, (
        f"{type_file.name}: example with correct type failed validation: "
        f"{[e.message for e in errors]}"
    )


@pytest.mark.parametrize("type_file", _PARSEABLE, ids=lambda p: p.name)
def test_wrong_type_rejected_by_const_constraint(type_file: Path) -> None:
    """Negative control: mutating ``type:`` to a wrong value triggers a ``const`` error.

    This test fails before the issue #86 fix (the constraint was silently
    dropped on load, so any value passed) and passes after the fix.
    """
    with type_file.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    examples = raw.get("examples") or []
    assert examples, f"{type_file.name}: expected at least one example"
    record = copy.deepcopy(examples[0])
    record["type"] = "shoebox"  # deliberately wrong

    schema = _resolved_schema_for_validation(type_file)
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(record))

    const_errors = [e for e in errors if e.validator == "const"]
    assert const_errors, (
        f"{type_file.name}: expected a `const` validation error after "
        f"setting type='shoebox', got errors: "
        f"{[(e.validator, e.message) for e in errors]}. The discriminator "
        f"is not being enforced — issue #86 regression."
    )

    # Sanity check: the const error names the field path `type` and the
    # offending value.
    const_err: ValidationError = const_errors[0]
    assert "type" in list(const_err.absolute_path) or const_err.absolute_path == (), (
        f"{type_file.name}: const error did not point at the `type` field: "
        f"path={list(const_err.absolute_path)}"
    )


# ---------------------------------------------------------------------------
# Meta-test: ensure we didn't accidentally lose coverage
# ---------------------------------------------------------------------------


def test_meta_all_nine_files_present() -> None:
    """We have a regression check for every type file from issue #9."""
    found = {f.stem for f in _list_type_files()}
    assert found == set(EXPECTED_TYPE_CONST.keys()), (
        f"set of type files changed: expected {sorted(EXPECTED_TYPE_CONST)}, "
        f"got {sorted(found)}"
    )
