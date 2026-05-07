"""Tabula classifier wake-signal package.

Implements the concrete realization of the SPEC's "sleep-API" pattern at the
infrastructure layer: the always-on classifier holds the authority to wake a
sleepable GPU worker on demand, using a *narrowly* scoped IAM permission
(``compute.instances.start`` against a single GPU instance ID — see #15).

This package provides:

* :func:`tabula_wake.wake_gpu.wake_gpu` — the underlying wake routine.
* :func:`tabula_wake.cli.main` — the ``/opt/tabula/wake-gpu`` CLI entrypoint.
* :func:`tabula_wake.server.create_app` — the internal-only HTTP server that
  exposes ``POST /wake`` for other classifier-resident processes (the Noise
  terminator from Epic #13) to trigger.

The package deliberately depends on as little as possible — a single-file Flask
app plus the Google Cloud Compute SDK — so the classifier VM's runtime
footprint stays small and easy to audit.

See ``README.md`` in this directory for the wake protocol, cold-start budget
and operational notes.
"""

# Note: we deliberately do NOT re-export ``wake_gpu`` (the function) at the
# package top level. Doing so would shadow the ``tabula_wake.wake_gpu``
# *module* attribute on the package, which breaks ``importlib.import_module``
# and ``monkeypatch.setattr`` flows that target module-level globals such as
# the default health check. Callers import from the submodule directly:
#
#     from tabula_wake.wake_gpu import wake_gpu, GpuInstanceConfig

from tabula_wake.wake_gpu import (
    GpuInstanceConfig,
    WakeOutcome,
    WakeResult,
    WakeTimeoutError,
)

__all__ = [
    "GpuInstanceConfig",
    "WakeOutcome",
    "WakeResult",
    "WakeTimeoutError",
]
