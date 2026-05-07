"""Schema YAML parse-cleanliness test (issues #87 and #94).

Several files in ``schema/`` previously contained YAML hazards that strict
parsers (PyYAML, ruamel.yaml strict, js-yaml) reject outright:

  - **#87**: unquoted regex quantifiers like ``{26}`` inside flow-style
    mappings (e.g. ``pattern: ^[0-9A-Z]{26}$``). YAML 1.2 treats unquoted
    ``{...}`` as flow-mapping syntax in some contexts.
  - **#94**: unquoted ``:`` inside ``pattern:`` values (e.g.
    ``pattern: ^person:[a-z0-9-]+$``). In flow-mapping context the ``:`` is
    genuinely ambiguous; in block context it's defensible but quoting is
    cheap insurance and required for cross-parser robustness (js-yaml is
    stricter than PyYAML).

After both fixes land, every schema YAML must parse cleanly under all three
parsers we exercise here. Scope is intentionally narrow:

  - It targets every YAML file under ``schema/v1/`` (the 9 type schemas plus
    ``frontmatter-base.yaml``) and the Luce extension under
    ``schema/luce/v1/extensions/decision.yaml``.
  - It does NOT validate the *meaning* of the schemas; it only asserts that
    the YAML loads cleanly.
  - The PyYAML leg is mandatory (PyYAML is a tiny pure-Python dep). The
    ``ruamel.yaml`` and ``js-yaml`` legs skip cleanly when the parser /
    runtime is not installed in the test environment.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest


# Walk up from this file to the repo root: tests/integration/<this file>.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCHEMA_ROOT = _REPO_ROOT / "schema"


# Every schema YAML file. After #94 lands, all of these must parse cleanly
# under every parser leg below. ``conversation.yaml`` was previously carved
# out because it contained additional unquoted-pattern bugs that #87 didn't
# fix; #94 finishes the cleanup so the carve-out is gone.
_TARGET_RELPATHS: tuple[str, ...] = (
    "schema/v1/frontmatter-base.yaml",
    "schema/v1/types/conversation.yaml",
    "schema/v1/types/decision.yaml",
    "schema/v1/types/event.yaml",
    "schema/v1/types/observation.yaml",
    "schema/v1/types/person.yaml",
    "schema/v1/types/place.yaml",
    "schema/v1/types/project.yaml",
    "schema/v1/types/tool.yaml",
    "schema/v1/types/vision.yaml",
    "schema/luce/v1/extensions/decision.yaml",
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


def _rel(path: pathlib.Path) -> str:
    """Repo-relative path string, for readable test IDs."""
    try:
        return str(path.relative_to(_REPO_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# PyYAML safe_load (always required)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _SCHEMA_FILES, ids=_rel)
def test_pyyaml_safe_load(path: pathlib.Path) -> None:
    """Every schema YAML must load cleanly under PyYAML ``safe_load``."""
    yaml = pytest.importorskip("yaml", reason="PyYAML not installed")
    with path.open(encoding="utf-8") as f:
        try:
            yaml.safe_load(f)
        except yaml.YAMLError as exc:  # pragma: no cover - failure path
            pytest.fail(f"PyYAML failed to parse {_rel(path)}: {exc}")


# ---------------------------------------------------------------------------
# ruamel.yaml strict (optional — skipped cleanly if not installed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _SCHEMA_FILES, ids=_rel)
def test_ruamel_strict_load(path: pathlib.Path) -> None:
    """Every schema YAML must load cleanly under ruamel.yaml strict."""
    ruamel_mod = pytest.importorskip(
        "ruamel.yaml", reason="ruamel.yaml not installed"
    )
    YAML = ruamel_mod.YAML
    YAMLError = getattr(ruamel_mod, "YAMLError", Exception)

    parser = YAML(typ="safe", pure=True)
    # NOTE: ``allow_duplicate_keys`` is intentionally left at the default
    # (``True``) here. ruamel's strict duplicate-key check would surface a
    # separate, unrelated bug (#86: duplicate ``properties`` keys in some
    # type schemas) that is OUT OF SCOPE for #87 and #94. The narrow purpose
    # of this leg is to verify that the flow-mapping fixes hold under
    # ruamel's generally-stricter parser. A separate cleanup PR can tighten
    # this once the duplicate-key carve-outs are no longer needed.
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


@pytest.mark.parametrize("path", _SCHEMA_FILES, ids=_rel)
def test_js_yaml_load(path: pathlib.Path) -> None:
    """Every schema YAML must load cleanly under js-yaml (if installed)."""
    ok, reason = _js_yaml_available()
    if not ok:
        pytest.skip(reason)

    # Run js-yaml.load(..) on the file; print "OK" on success, exit non-zero
    # with the parser error on failure. ``json: true`` selects JSON-compatible
    # behavior so duplicate keys (an unrelated #86 bug) don't fail this leg —
    # we only care about the flow-mapping fixes here, not duplicate-key
    # strict detection.
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
# Textual fix-verification (regression guards for #87 and #94)
# ---------------------------------------------------------------------------


# The seven lines enumerated by the curator in #87. Every occurrence uses the
# same ULID pattern; every occurrence MUST be wrapped in double quotes after
# the fix. (Line numbers reflect the file state after both #87 and #94
# have landed.)
_AFFECTED_LINES_87: tuple[tuple[str, int], ...] = (
    ("schema/v1/frontmatter-base.yaml", 24),
    ("schema/v1/frontmatter-base.yaml", 117),
    ("schema/v1/frontmatter-base.yaml", 121),
    ("schema/v1/frontmatter-base.yaml", 125),
    ("schema/v1/frontmatter-base.yaml", 129),
    ("schema/v1/types/observation.yaml", 64),
    ("schema/v1/types/conversation.yaml", 66),
)

_QUOTED_ULID = '"^[0-9A-HJKMNP-TV-Z]{26}$"'
_UNQUOTED_ULID_BARE = "^[0-9A-HJKMNP-TV-Z]{26}$"


@pytest.mark.parametrize(
    ("relpath", "lineno"),
    _AFFECTED_LINES_87,
    ids=lambda v: str(v),
)
def test_targeted_lines_are_quoted(relpath: str, lineno: int) -> None:
    """Each of the seven #87 lines must contain the QUOTED ULID pattern.

    Regression guard: catches anyone who reverts a single line back to the
    unquoted form.
    """
    path = _REPO_ROOT / relpath
    assert path.is_file(), f"missing schema file: {relpath}"
    text = path.read_text(encoding="utf-8").splitlines()
    assert lineno <= len(text), (
        f"{relpath} has only {len(text)} lines; expected line {lineno} "
        f"to contain the ULID pattern (file may have been refactored — "
        f"update _AFFECTED_LINES_87 if so)."
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


# Regression guard for #94: any ``pattern:`` value containing ``:`` must be
# wrapped in quotes. Matches both block-style (``pattern: ^x:y$``) and
# flow-style (``pattern: ^x:y$}``) occurrences. The regex looks for
# ``pattern:``, optional whitespace, then a value that starts with a
# non-quote character and contains a ``:`` somewhere before the line's
# terminator (``$`` or ``}``). After the fix, this scan should yield zero
# matches across every schema file.
_UNQUOTED_PATTERN_COLON_RE = re.compile(
    r"pattern:\s+[^\"'\s].*:.*[\$\}]"
)


@pytest.mark.parametrize("path", _SCHEMA_FILES, ids=_rel)
def test_no_unquoted_colon_in_pattern_values(path: pathlib.Path) -> None:
    """No schema YAML may contain an unquoted ``:`` inside a ``pattern:`` value.

    Regression guard for #94: catches both block-style
    (``pattern: ^person:[a-z0-9-]+$``) and flow-style
    (``items: {... pattern: ^person:[a-z0-9-]+$}``) occurrences. After the
    fix, every ``pattern:`` value containing a ``:`` must be wrapped in
    double quotes.
    """
    text = path.read_text(encoding="utf-8")
    offenders: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _UNQUOTED_PATTERN_COLON_RE.search(line):
            offenders.append((lineno, line))
    assert not offenders, (
        f"{_rel(path)}: found unquoted ``:`` inside ``pattern:`` values "
        f"(should be wrapped in double quotes for cross-parser robustness):\n"
        + "\n".join(f"  line {n}: {ln!r}" for n, ln in offenders)
    )


# ---------------------------------------------------------------------------
# Sanity: we actually discovered the targeted files
# ---------------------------------------------------------------------------


def test_schema_yaml_collection_nonempty() -> None:
    """Guard: every targeted file must exist on disk.

    This fails loudly if a future refactor moves any of the targeted files
    so the parser tests can't silently turn into a green no-op.
    """
    assert _SCHEMA_ROOT.is_dir(), (
        f"schema directory not found at {_SCHEMA_ROOT} — did the repo layout "
        f"change? Update tests/integration/test_schema_yaml_parses.py."
    )
    found = {_rel(p) for p in _SCHEMA_FILES}
    expected = set(_TARGET_RELPATHS)
    missing = expected - found
    assert not missing, (
        f"expected schema files missing on disk: {sorted(missing)}; "
        f"update _TARGET_RELPATHS in this test if the layout changed."
    )
