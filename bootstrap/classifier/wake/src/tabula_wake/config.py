"""Configuration loading for the wake helper.

Two sources, tried in order:

1. **GCE instance metadata** — the canonical source on a real classifier VM.
   Terraform writes the GPU instance name + zone into the classifier VM's
   metadata at apply time (keys ``tabula-gpu-instance-name``,
   ``tabula-gpu-instance-zone``, ``tabula-gpu-health-url``,
   ``tabula-gpu-project-id``). The metadata server is reachable at
   ``http://metadata.google.internal`` from inside any GCE VM.

2. **A YAML/JSON config file** — fallback for local development, integration
   tests, and any environment without a metadata server. Path can be
   overridden via the ``TABULA_WAKE_CONFIG`` env var; defaults to
   ``/etc/tabula/wake.json``.

Either source produces a :class:`tabula_wake.wake_gpu.GpuInstanceConfig`.

We deliberately do *not* parse YAML in the helper — the classifier VM's
runtime image already has enough surface; sticking to JSON avoids pulling
in PyYAML for a trivial config blob.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from tabula_wake.wake_gpu import GpuInstanceConfig

_METADATA_BASE = "http://metadata.google.internal/computeMetadata/v1/instance/attributes"
_METADATA_HEADER = {"Metadata-Flavor": "Google"}

_META_KEYS = {
    "project_id": "tabula-gpu-project-id",
    "zone": "tabula-gpu-instance-zone",
    "instance_name": "tabula-gpu-instance-name",
    "health_url": "tabula-gpu-health-url",
}

DEFAULT_CONFIG_PATH = Path("/etc/tabula/wake.json")


def load(
    *,
    config_path: Optional[Path] = None,
    metadata_fetch: Optional[callable] = None,  # type: ignore[type-arg]
) -> GpuInstanceConfig:
    """Load the wake target config.

    Parameters
    ----------
    config_path:
        Override for the file fallback. Defaults to
        ``$TABULA_WAKE_CONFIG`` or ``/etc/tabula/wake.json``.
    metadata_fetch:
        Override for metadata fetching. Tests pass a fake here.

    Raises
    ------
    RuntimeError
        If neither metadata nor a config file yields all four required
        fields.
    """
    fetch = metadata_fetch or _fetch_metadata_attribute
    values: dict[str, Optional[str]] = {k: None for k in _META_KEYS}

    # 1. Metadata server.
    for field, key in _META_KEYS.items():
        try:
            v = fetch(key)
        except Exception:  # noqa: BLE001 — metadata server may be absent
            v = None
        if v:
            values[field] = v

    # 2. File fallback for any field still missing.
    file_values = _load_file(config_path)
    for field in _META_KEYS:
        if values[field] is None and field in file_values:
            values[field] = file_values[field]

    missing = [f for f, v in values.items() if not v]
    if missing:
        raise RuntimeError(
            "wake config missing required fields: "
            + ", ".join(missing)
            + " (checked GCE metadata and config file)"
        )

    return GpuInstanceConfig(
        project_id=values["project_id"],  # type: ignore[arg-type]
        zone=values["zone"],  # type: ignore[arg-type]
        instance_name=values["instance_name"],  # type: ignore[arg-type]
        health_url=values["health_url"],  # type: ignore[arg-type]
    )


def _fetch_metadata_attribute(name: str, *, timeout: float = 1.0) -> Optional[str]:
    """GET a single metadata attribute. Returns ``None`` if the server is
    unreachable or returns 404 (the latter happens when a key isn't set,
    e.g. during local development).
    """
    url = f"{_METADATA_BASE}/{name}"
    req = urllib.request.Request(url, headers=_METADATA_HEADER)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if 200 <= resp.status < 300:
                return resp.read().decode("utf-8").strip()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except urllib.error.URLError:
        return None
    return None


def _load_file(config_path: Optional[Path]) -> dict[str, str]:
    path = config_path or Path(
        os.environ.get("TABULA_WAKE_CONFIG", str(DEFAULT_CONFIG_PATH))
    )
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"wake config at {path} is not a JSON object")
    return {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
