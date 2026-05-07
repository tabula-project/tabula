"""Schema YAML parse-cleanliness test (issue #87).

Several files in ``schema/v1/`` previously contained unquoted regex quantifiers
like ``{26}`` inside flow-style mappings (e.g. ``pattern: ^[0-9A-Z]{26}$``).
YAML 1.2 treats unquoted ``{...}`` as flow-mapping syntax in some contexts,
so strict parsers (PyYAML, ruamel.yaml strict, js-yaml) reject these files
outright.

This test exercises the three files touched by issue #87 against each parser
we have available and asserts a clean parse with no errors. Scope is
intentionally narrow:

  - It targets ONLY the files enumerated in issue #87 (``frontmatter-base.yaml``,
    ``types/observation.yaml``, ``types/conversation.yaml``). Other files in
    ``schema/v1/types/`` have a separate, independent flow-mapping issue
    involving unquoted ``:`` inside patterns like ``^person:[a-z0-9-]+$`` —
    that is out of scope for #87 and tracked elsewhere.
  - It does NOT validate the *meaning* of the schemas; it only asserts that
    the YAML loads cleanly.
  - The PyYAML leg is mandatory (PyYAML is a tiny pure-Python dep). The
    ``ruamel.yaml`` and ``js-yaml`` legs skip cleanly when the parser /
    runtime is not installed in the test environment.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest


# Walk up from this file to the repo root: tests/integration/<this file>.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCHEMA_ROOT = _REPO_ROOT / "schema"


# Files explicitly enumerated in issue #87 (the seven affected lines all live
# in these three files). Keep this list narrow; do not expand to the rest of
# ``schema/v1/types/`` until the separate flow-mapping-colon issue is fixed.
#
# NOTE on ``conversation.yaml``: that file is in the #87 fix list (line 67),
# but it ALSO contains independent flow-mapping bugs at lines 33 and 79
# involving unquoted ``:`` in patterns like ``^person:[a-z0-9-]+$``. Those
# are out of scope for #87 (a separate fix). To keep this test green and
# strictly scoped, ``conversation.yaml`` is excluded from the
# ``test_*_load`` parser parametrize lists below; its #87-specific
# transformation is verified textually by ``test_targeted_lines_are_quoted``.
_TARGET_RELPATHS: tuple[str, ...] = (
    "schema/v1/frontmatter-base.yaml",
    "schema/v1/types/observation.yaml",
    "schema/v1/types/conversation.yaml",
)

# Subset that parses cleanly after JUST the #87 fix (i.e. excludes the file
# with additional out-of-scope unquoted-pattern bugs).
_PARSE_TEST_RELPATHS: tuple[str, ...] = (
    "schema/v1/frontmatter-base.yaml",
    "schema/v1/types/observation.yaml",
)


def _resolve(relpaths: tuple[str, ...]) -> list[pathlib.Path]:
    """Resolve a tuple of repo-relative paths to absolute paths.

    Returns an empty list when the schema tree is missing entirely so a
    stripped-down checkout doesn't fail collection — the
    ``test_schema_yaml_collection_nonempty`` guard below catches that case
    explicitly with a helpful message.
    """
    if not _SCHEMA_ROOT.is_dir():
        return []
    out: list[pathlib.Path] = []
    for rel in relpaths:
        p = _REPO_ROOT / rel
        if p.is_file():
            out.append(p)
    return out


_SCHEMA_FILES = _resolve(_TARGET_RELPATHS)
_PARSE_TEST_FILES = _resolve(_PARSE_TEST_RELPATHS)


def _rel(path: pathlib.Path) -> str:
    """Repo-relative path string, for readable test IDs."""
    try:
        return str(path.relative_to(_REPO_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# PyYAML safe_load (always required)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _PARSE_TEST_FILES, ids=_rel)
def test_pyyaml_safe_load(path: pathlib.Path) -> None:
    """Every #87-targeted schema YAML must load cleanly under PyYAML ``safe_load``."""
    yaml = pytest.importorskip("yaml", reason="PyYAML not installed")
    with path.open(encoding="utf-8") as f:
        try:
            yaml.safe_load(f)
        except yaml.YAMLError as exc:  # pragma: no cover - failure path
            pytest.fail(f"PyYAML failed to parse {_rel(path)}: {exc}")


# ---------------------------------------------------------------------------
# ruamel.yaml strict (optional — skipped cleanly if not installed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _PARSE_TEST_FILES, ids=_rel)
def test_ruamel_strict_load(path: pathlib.Path) -> None:
    """Every #87-targeted schema YAML must load cleanly under ruamel.yaml strict."""
    ruamel_mod = pytest.importorskip(
        "ruamel.yaml", reason="ruamel.yaml not installed"
    )
    YAML = ruamel_mod.YAML
    YAMLError = getattr(ruamel_mod, "YAMLError", Exception)

    parser = YAML(typ="safe", pure=True)
    # NOTE: ``allow_duplicate_keys`` is intentionally left at the default
    # (``True``) here. ruamel's strict duplicate-key check would surface a
    # separate, unrelated bug (#86: duplicate ``properties`` keys in some
    # type schemas) that is OUT OF SCOPE for #87. The narrow purpose of this
    # leg is to verify that #87's flow-mapping fix holds under ruamel's
    # generally-stricter parser. When #86 lands, this comment can be removed
    # and strict duplicate detection re-enabled.
    parser.allow_duplicate_keys = True

    with path.open(encoding="utf-8") as f:
        try:
            parser.load(f)
        except YAMLError as exc:  # pragma: no cover - failure path
            pytest.fail(f"ruamel.yaml failed to parse {_rel(path)}: {exc}")


# ---------------------------------------------------------------------------
# js-yaml (optional — runs only when Node + js-yaml are both available)
# ---------------------------------------------------------------------------


def _js_yaml_available() -> tuple[bool, str]:
    """Return ``(ok, reason_if_skipped)`` for the js-yaml leg.

    Requires both a ``node`` binary on PATH and a resolvable ``js-yaml``
    module from that node. We probe with ``require.resolve`` so the test
    skips cleanly when the dependency isn't installed.
    """
    node = shutil.which("node")
    if node is None:
        return False, "node not on PATH"
    probe = subprocess.run(
        [node, "-e", "require.resolve('js-yaml')"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return False, "js-yaml not resolvable from node"
    return True, ""


@pytest.mark.parametrize("path", _PARSE_TEST_FILES, ids=_rel)
def test_js_yaml_load(path: pathlib.Path) -> None:
    """Every #87-targeted schema YAML must load cleanly under js-yaml (if installed)."""
    ok, reason = _js_yaml_available()
    if not ok:
        pytest.skip(reason)

    # Run js-yaml.load(..) on the file; print "OK" on success, exit non-zero
    # with the parser error on failure. ``json: true`` selects JSON-compatible
    # behavior so duplicate keys (an unrelated #86 bug) don't fail this leg —
    # we only care about the flow-mapping fix here, not duplicate-key strict
    # detection.
    script = (
        "const fs = require('fs'); const yaml = require('js-yaml'); "
        "try { yaml.load(fs.readFileSync(process.argv[1], 'utf8'), "
        "{ json: true }); "
        "process.stdout.write('OK'); } "
        "catch (e) { process.stderr.write(e.message || String(e)); "
        "process.exit(1); }"
    )
    result = subprocess.run(
        ["node", "-e", script, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:  # pragma: no cover - failure path
        pytest.fail(
            f"js-yaml failed to parse {_rel(path)}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# Textual fix-verification (covers all 7 #87 lines, including conversation.yaml)
# ---------------------------------------------------------------------------


# The seven lines enumerated by the curator in #87. Every occurrence uses the
# same ULID pattern; every occurrence MUST be wrapped in double quotes after
# the fix. The textual assertions below catch a regression even on
# ``conversation.yaml``, which can't be parsed end-to-end yet because of an
# unrelated, out-of-scope bug.
_AFFECTED_LINES: tuple[tuple[str, int], ...] = (
    ("schema/v1/frontmatter-base.yaml", 24),
    ("schema/v1/frontmatter-base.yaml", 117),
    ("schema/v1/frontmatter-base.yaml", 121),
    ("schema/v1/frontmatter-base.yaml", 125),
    ("schema/v1/frontmatter-base.yaml", 129),
    ("schema/v1/types/observation.yaml", 65),
    ("schema/v1/types/conversation.yaml", 67),
)

_QUOTED_ULID = '"^[0-9A-HJKMNP-TV-Z]{26}$"'
_UNQUOTED_ULID_BARE = "^[0-9A-HJKMNP-TV-Z]{26}$"


@pytest.mark.parametrize(
    ("relpath", "lineno"),
    _AFFECTED_LINES,
    ids=lambda v: str(v),
)
def test_targeted_lines_are_quoted(relpath: str, lineno: int) -> None:
    """Each of the seven #87 lines must contain the QUOTED ULID pattern.

    Regression guard: catches anyone who reverts a single line back to the
    unquoted form, even on files whose full parse is blocked by an unrelated
    out-of-scope bug.
    """
    path = _REPO_ROOT / relpath
    assert path.is_file(), f"missing schema file: {relpath}"
    text = path.read_text(encoding="utf-8").splitlines()
    assert lineno <= len(text), (
        f"{relpath} has only {len(text)} lines; expected line {lineno} "
        f"to contain the ULID pattern (file may have been refactored — "
        f"update _AFFECTED_LINES if so)."
    )
    line = text[lineno - 1]
    assert _QUOTED_ULID in line, (
        f"{relpath}:{lineno} is missing the QUOTED ULID pattern.\n"
        f"  expected to contain: {_QUOTED_ULID}\n"
        f"  actual line:         {line!r}"
    )
    # And explicitly: there must NOT be a bare unquoted occurrence on this
    # line (i.e. ``pattern: ^[0-9A-HJKMNP-TV-Z]{26}$`` with no surrounding
    # quote). This catches accidental reverts to the buggy form.
    bare_appears = _UNQUOTED_ULID_BARE in line
    quoted_appears = _QUOTED_ULID in line
    # If the quoted form appears, the bare substring trivially also appears
    # *inside* the quotes — so the unquoted-only check is: bare appears AND
    # quoted does NOT.
    assert not (bare_appears and not quoted_appears), (
        f"{relpath}:{lineno} contains the unquoted ULID pattern; "
        f"#87 requires it to be wrapped in double quotes."
    )


# ---------------------------------------------------------------------------
# Sanity: we actually discovered the targeted files
# ---------------------------------------------------------------------------


def test_schema_yaml_collection_nonempty() -> None:
    """Guard: every targeted file must exist on disk.

    This fails loudly if a future refactor moves any of the #87-affected
    files so the parser tests can't silently turn into a green no-op.
    """
    assert _SCHEMA_ROOT.is_dir(), (
        f"schema directory not found at {_SCHEMA_ROOT} — did the repo layout "
        f"change? Update tests/integration/test_schema_yaml_parses.py."
    )
    found = {_rel(p) for p in _SCHEMA_FILES}
    expected = set(_TARGET_RELPATHS)
    missing = expected - found
    assert not missing, (
        f"expected #87-targeted schema files missing on disk: {sorted(missing)}; "
        f"update _TARGET_RELPATHS in this test if the layout changed."
    )
