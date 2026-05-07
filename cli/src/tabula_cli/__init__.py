"""tabula-cli — user-facing CLI for the Tabula enclave lifecycle.

Subcommands (current):

* ``tabula enclave down <name>`` — tear an enclave down to zero residual cost.

Subcommands (planned, not yet implemented in this package):

* ``tabula enclave up <name>`` — provision an enclave (issue #26)
* ``tabula enclave status <name>`` — health check (issue #30)
* ``tabula enclave ssh <name> {classifier|gpu|gitea}`` — IAP-tunneled shell (issue #33)

The :mod:`tabula_cli._enclave_state` module is the **shared schema** for the
on-disk per-enclave state file. ``up`` writes it, ``down`` reads it. The
schema is versioned (``version: 1``); see that module's docstring for the
contract.
"""

from tabula_cli._enclave_state import (
    ENCLAVE_LABEL_KEY,
    STATE_SCHEMA_VERSION,
    EnclaveState,
)

__version__ = "0.1.0"

__all__ = [
    "ENCLAVE_LABEL_KEY",
    "STATE_SCHEMA_VERSION",
    "EnclaveState",
]
