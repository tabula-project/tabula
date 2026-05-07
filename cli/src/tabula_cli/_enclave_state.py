"""Shared state.json schema and helpers for `tabula enclave` subcommands.

This module is the single source of truth for the on-disk per-enclave state
file at ``~/.tabula/enclaves/<name>/state.json``. It is shared between
``tabula enclave up`` (writer; see issue #26) and ``tabula enclave down``
(reader; see issue #28), plus future ``status`` and ``ssh`` subcommands.

The schema is **versioned** (``version: 1``) and treated as a contract.
Bumping the version requires an explicit migration path; ``down`` rejects
unknown versions with a clear error so a partially-upgraded toolchain cannot
silently mishandle an existing enclave.

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
from pathlib import Path
from typing import Any

#: Current state.json schema version. Bumping requires migration support.
STATE_SCHEMA_VERSION: int = 1

#: Label key applied by the Terraform root module to every enclave-owned
#: resource. ``--force`` recovery and post-destroy verification both rely on
#: it; if a Terraform module fails to apply this label its resources will
#: leak silently.
ENCLAVE_LABEL_KEY: str = "enclave"

#: Default root for per-host enclave state directories.
DEFAULT_ENCLAVES_ROOT: Path = Path.home() / ".tabula" / "enclaves"

#: DNS-safe enclave name regex. Mirrors the validator used by ``up`` (#26).
_NAME_RE = re.compile(r"^[a-z]([-a-z0-9]{1,28}[a-z0-9])?$")


class StateError(Exception):
    """Base class for state-file errors."""


class StateNotFoundError(StateError):
    """Raised when the per-enclave state directory or state.json is missing."""


class StateCorruptError(StateError):
    """Raised when state.json is unreadable or fails schema validation."""


class StateVersionError(StateError):
    """Raised when state.json carries an unsupported schema version."""


@dataclass(frozen=True)
class EnclaveState:
    """In-memory representation of state.json (version 1).

    Frozen because callers must not mutate state in place; rewrite via
    :func:`write_state` if changes are intended.
    """

    name: str
    project_id: str
    region: str
    created_at: str
    terraform_dir: str
    outputs: dict[str, Any] = field(default_factory=dict)
    version: int = STATE_SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
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


def is_valid_name(name: str) -> bool:
    """Return True iff ``name`` matches the DNS-safe enclave-name regex.

    Mirrors the validator in ``tabula enclave up`` (#26). Kept here so that
    ``down`` can sanity-check user input before touching the filesystem or
    making cloud API calls.
    """
    return bool(_NAME_RE.match(name))


def enclave_dir(name: str, root: Path | None = None) -> Path:
    """Return the per-enclave state directory path."""
    return (root or DEFAULT_ENCLAVES_ROOT) / name


def state_path(name: str, root: Path | None = None) -> Path:
    """Return the path to ``state.json`` for an enclave."""
    return enclave_dir(name, root) / "state.json"


def read_state(name: str, root: Path | None = None) -> EnclaveState:
    """Read and validate ``state.json`` for ``name``.

    Raises:
        StateNotFoundError: if the directory or file is missing.
        StateCorruptError: if the JSON is unreadable or required fields missing.
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
            f"state.json for '{name}' is unreadable: {exc}. "
            f"Use --force to attempt recovery via cloud labels."
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
    """Write ``state`` to disk atomically and return the final path.

    Provided for completeness (``up`` is the primary writer). Uses an atomic
    rename so a crash mid-write cannot corrupt an existing state file.
    """
    path = state_path(state.name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.to_json(), indent=2) + "\n", encoding="utf-8")
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
