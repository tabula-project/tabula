"""Shared state.json schema and helpers for `tabula enclave` subcommands.

This module is the single source of truth for the on-disk per-enclave state
file at ``~/.tabula/enclaves/<name>/state.json``. It is shared between
``tabula enclave up`` (writer; see issue #26) and ``tabula enclave down``
(reader; see issue #28), plus future ``status`` and ``ssh`` subcommands.

The schema is **versioned** (``version: 1``) and treated as a contract.
Bumping the version requires an explicit migration path; both ``up`` and
``down`` reject unknown versions with a clear error so a partially-upgraded
toolchain cannot silently mishandle an existing enclave.

Schema (version 1):

    {
      "version": 1,
      "name": "demo",
      "project_id": "tabula-demo-123",
      "region": "us-central1",
      "created_at": "2026-05-07T12:00:00Z",
      "terraform_dir": "/Users/x/.tabula/enclaves/demo/terraform",
      "outputs": { "classifier_ip": "...", "noise_port": 51820 }
    }

All enclave-owned cloud resources MUST carry the label ``enclave=<name>``.
The label is the only authoritative way to find leftovers when state is
missing or corrupt; ``down --force`` relies on it exclusively.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Current state.json schema version. Bumping requires migration support.
STATE_SCHEMA_VERSION: int = 1

#: Backwards-compatible alias used by the ``up`` writer (#26).
CURRENT_SCHEMA_VERSION: int = STATE_SCHEMA_VERSION

#: Label key applied by the Terraform root module to every enclave-owned
#: resource. ``--force`` recovery and post-destroy verification both rely on
#: it; if a Terraform module fails to apply this label its resources will
#: leak silently.
ENCLAVE_LABEL_KEY: str = "enclave"

#: DNS-safe enclave name regex. 3-30 chars, lowercase, starts with a
#: letter, ends with a letter or digit, hyphens allowed in the middle.
_NAME_RE = re.compile(r"^[a-z]([-a-z0-9]{1,28}[a-z0-9])?$")


class StateError(Exception):
    """Base class for state-file errors."""


class StateNotFoundError(StateError):
    """Raised when the per-enclave state directory or state.json is missing."""


class StateCorruptError(StateError):
    """Raised when state.json is unreadable or fails schema validation."""


class StateVersionError(StateError):
    """Raised when state.json carries an unsupported schema version."""


class EnclaveNameError(ValueError):
    """Raised when an enclave name fails validation (writer-side, ``up``)."""


# Compatibility alias used by ``up``-side callers that catch a single
# "schema problem" exception class. ``read_state`` raises the more specific
# :class:`StateCorruptError` / :class:`StateVersionError`; both are subclasses
# of :class:`StateError`, so handlers using ``StateSchemaError`` will catch
# either one.
StateSchemaError = StateError


def default_root() -> Path:
    """Return the per-user enclave-state root directory.

    Honors the ``TABULA_HOME`` environment variable for tests / non-default
    layouts (the writer sets this in unit tests; the reader picks it up the
    same way). Always returns an absolute :class:`Path`.
    """
    override = os.environ.get("TABULA_HOME")
    if override:
        return (Path(override).expanduser() / "enclaves").resolve()
    return (Path.home() / ".tabula" / "enclaves").resolve()


@dataclass
class EnclaveState:
    """In-memory representation of state.json (version 1).

    Mutable for ergonomics on the writer side (``up`` constructs and adjusts
    instances in flight). Persistence is via :func:`write_state`; concurrent
    mutation is the caller's responsibility.
    """

    name: str
    project_id: str
    region: str
    created_at: str
    terraform_dir: str
    outputs: dict[str, Any] = field(default_factory=dict)
    version: int = STATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict matching the on-disk schema."""
        d = asdict(self)
        # Field order: version first for human-readability.
        return {
            "version": d["version"],
            "name": d["name"],
            "project_id": d["project_id"],
            "region": d["region"],
            "created_at": d["created_at"],
            "terraform_dir": d["terraform_dir"],
            "outputs": d["outputs"],
        }

    # Backwards-compatible alias used by some readers.
    def to_json(self) -> dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EnclaveState":
        """Deserialize, enforcing schema-version compatibility.

        Raises:
            StateSchemaError: If the version is unsupported or required keys
                are missing. (StateSchemaError is an alias for StateError;
                more specific subclasses :class:`StateVersionError` and
                :class:`StateCorruptError` are also raised.)
        """
        version = raw.get("version")
        if version != STATE_SCHEMA_VERSION:
            raise StateVersionError(
                f"state.json schema version {version!r} is not supported by "
                f"this CLI (expects {STATE_SCHEMA_VERSION}). "
                "Upgrade the CLI or remove the enclave directory."
            )
        try:
            return cls(
                name=raw["name"],
                project_id=raw["project_id"],
                region=raw["region"],
                created_at=raw["created_at"],
                terraform_dir=raw["terraform_dir"],
                outputs=raw.get("outputs", {}) or {},
                version=version,
            )
        except KeyError as e:
            raise StateCorruptError(
                f"state.json is missing required key: {e.args[0]!r}"
            ) from e


def is_valid_name(name: str) -> bool:
    """Return True iff ``name`` matches the DNS-safe enclave-name regex.

    Non-raising sibling of :func:`validate_enclave_name`. Used by ``down``
    (#28) to sanity-check user input before touching the filesystem or
    making cloud API calls.
    """
    return bool(isinstance(name, str) and _NAME_RE.match(name))


def validate_enclave_name(name: str) -> None:
    """Validate an enclave name; raise :class:`EnclaveNameError` if invalid.

    Used by ``up`` (#26) which prefers raising over a boolean to surface a
    concrete remediation message.
    """
    if not isinstance(name, str) or not name:
        raise EnclaveNameError("enclave name must be a non-empty string")
    if not (3 <= len(name) <= 30):
        raise EnclaveNameError(
            f"enclave name must be 3-30 characters; got {len(name)} ({name!r})"
        )
    if not _NAME_RE.match(name):
        raise EnclaveNameError(
            f"enclave name {name!r} is not DNS-safe; "
            "must match ^[a-z]([-a-z0-9]{1,28}[a-z0-9])?$ "
            "(lowercase, start with a letter, end with letter or digit, "
            "hyphens allowed in the middle)"
        )


def enclave_dir(name: str, root: Path | None = None) -> Path:
    """Return the per-enclave state directory path."""
    base = root if root is not None else default_root()
    return base / name


def state_path(name: str, root: Path | None = None) -> Path:
    """Return the path to ``state.json`` for an enclave."""
    return enclave_dir(name, root) / "state.json"


def state_exists(name: str, root: Path | None = None) -> bool:
    """Return True iff ``state.json`` already exists for ``name``."""
    return state_path(name, root).exists()


def now_utc_iso() -> str:
    """Return current UTC time as an ISO-8601 string with trailing ``Z``."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def read_state(name: str, root: Path | None = None) -> EnclaveState:
    """Read and validate ``state.json`` for ``name``.

    Raises:
        StateNotFoundError: if the file is missing. (Subclass of
            :class:`StateError`; ``up``'s idempotent re-apply path catches
            this via ``StateSchemaError``-the-alias, ``down`` catches the
            specific subclass.)
        StateCorruptError: if the JSON is unreadable or required fields
            missing.
        StateVersionError: if ``version`` is not :data:`STATE_SCHEMA_VERSION`.
    """
    path = state_path(name, root)
    if not path.exists():
        raise StateNotFoundError(
            f"No state file for enclave '{name}' at {path}. "
            f"Use --force to attempt recovery via cloud labels."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateCorruptError(
            f"state.json for '{name}' is unreadable: {exc}."
        ) from exc

    if not isinstance(raw, dict):
        raise StateCorruptError(
            f"state.json for '{name}' is not a JSON object."
        )

    version = raw.get("version")
    if version != STATE_SCHEMA_VERSION:
        raise StateVersionError(
            f"state.json for '{name}' has version={version!r}; "
            f"this CLI supports version={STATE_SCHEMA_VERSION}. "
            f"Upgrade the CLI or migrate state."
        )

    required = ("name", "project_id", "region", "created_at", "terraform_dir")
    missing = [k for k in required if k not in raw]
    if missing:
        raise StateCorruptError(
            f"state.json for '{name}' is missing required fields: {missing}"
        )

    if raw["name"] != name:
        raise StateCorruptError(
            f"state.json name field {raw['name']!r} does not match directory name {name!r}"
        )

    return EnclaveState(
        version=version,
        name=raw["name"],
        project_id=raw["project_id"],
        region=raw["region"],
        created_at=raw["created_at"],
        terraform_dir=raw["terraform_dir"],
        outputs=raw.get("outputs", {}) or {},
    )


def write_state(state: EnclaveState, root: Path | None = None) -> Path:
    """Write ``state`` to disk atomically and return the final path."""
    if not is_valid_name(state.name):
        raise EnclaveNameError(
            f"refusing to write state for invalid enclave name {state.name!r}"
        )
    path = state_path(state.name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def remove_state_dir(name: str, root: Path | None = None) -> None:
    """Recursively remove the per-enclave state directory.

    No-op if the directory is already absent (so callers can use this in
    idempotent teardown paths).
    """
    d = enclave_dir(name, root)
    if not d.exists():
        return
    # Manual recursive remove keeps the dependency surface small and avoids
    # surprising behaviour from shutil.rmtree on symlinks.
    for child in sorted(d.rglob("*"), reverse=True):
        if child.is_dir() and not child.is_symlink():
            child.rmdir()
        else:
            child.unlink()
    d.rmdir()


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_ENCLAVES_ROOT",
    "ENCLAVE_LABEL_KEY",
    "EnclaveNameError",
    "EnclaveState",
    "STATE_SCHEMA_VERSION",
    "StateCorruptError",
    "StateError",
    "StateNotFoundError",
    "StateSchemaError",
    "StateVersionError",
    "default_root",
    "enclave_dir",
    "is_valid_name",
    "now_utc_iso",
    "read_state",
    "remove_state_dir",
    "state_exists",
    "state_path",
    "validate_enclave_name",
    "write_state",
]


#: Backwards-compatible: ``down`` (#28) and other readers reference this
#: constant. Use :func:`default_root` instead in new code so ``TABULA_HOME``
#: overrides apply correctly.
DEFAULT_ENCLAVES_ROOT: Path = (Path.home() / ".tabula" / "enclaves")
