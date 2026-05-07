# Tabula classifier wake-signal helper

Concrete realization of the SPEC's "sleep-API" pattern at the infrastructure
layer. The classifier VM (always-on, cheap-warm) holds a narrowly scoped IAM
permission to start one specific GPU VM, and this package is the only code
that exercises it.

> Implements **issue #37** (parent #12).
>
> Hard dependencies:
> - **#15** — IAM module that defines the `${enclave}_gpu_waker` custom role
>   (permissions: `compute.instances.get`, `compute.instances.start`),
>   bound to the classifier SA with a condition pinning the grant to one
>   instance ID.
> - **#17** — classifier VM (host for this code).
> - **#19** — GPU VM (the wake target).
> - **#24** — firewall module providing the internal-only allow rule for
>   the wake port (default `8088`).
>
> Soft dependency:
> - **#35** — GPU bootstrap defines the agent health endpoint that the
>   polling loop hits.

## Wake protocol

There are two surfaces, equivalent under the hood:

1. **`/opt/tabula/wake-gpu` CLI.** A console script (`wake-gpu` from
   `pyproject.toml`) installed on the classifier VM. Operators use this for
   debugging; the GPU bootstrap doc (#35) also calls it as part of its
   smoke tests.

2. **`POST /wake` HTTP endpoint** on the classifier, default port `8088`,
   bound to `127.0.0.1` and the classifier's internal NIC IP only. This is
   the production path — the Noise terminator from Epic #13 calls it.

Both produce a structured outcome:

| outcome             | meaning                                                |
|---------------------|--------------------------------------------------------|
| `already_running`   | VM was up; agent health endpoint responded 2xx.        |
| `started`           | We issued `instances.start`; VM came up; agent ready.  |
| `timeout`           | Wall-clock budget exceeded before VM-RUNNING and/or agent-ready. |
| `permission_denied` | Classifier SA lacks `compute.instances.start`/`get`.   |
| `api_error`         | Other GCE API error.                                   |

HTTP status codes:

| outcome             | status |
|---------------------|--------|
| `already_running`   | 200    |
| `started`           | 200    |
| `timeout`           | 504    |
| `permission_denied` | 403    |
| `api_error`         | 502    |

CLI exit codes:

| outcome             | exit |
|---------------------|------|
| `already_running`   | 0    |
| `started`           | 0    |
| `timeout`           | 2    |
| `permission_denied` | 3    |
| `api_error`         | 1    |

## Cold-start budget

End-to-end timing for a wake from cold:

| stage                                                     | time      |
|-----------------------------------------------------------|-----------|
| GCE `instances.start` API call → VM RUNNING (T4)          | 30–60 s   |
| VM RUNNING → bootstrap (#35) complete + agent health green| 10–30 s on first boot, faster on subsequent |
| **Total**                                                 | **40–90 s** |

The default wake timeout is `90 s`. The CLI takes `--timeout`; the HTTP
endpoint takes `?timeout=`. Both are bounded; there is no retry loop. If
callers want retries, they wrap the wake call.

Demo and UI affordances should calibrate to this window — the Epic #13
client surfaces a "warming up..." indicator while a wake is in flight.

## Idempotency

Two layers prevent duplicate `instances.start` calls:

1. **In-process lock** — a single `threading.Lock` serializes wake attempts
   from this classifier process. Two near-simultaneous `POST /wake` calls
   both return success, but only one issues `instances.start`.
2. **GCE API** — calling `instances.start` on a VM in `RUNNING` is a no-op
   that returns success. The narrow IAM permission means we cannot call
   `stop` or `delete` even by mistake.

Cross-process / cross-host concurrency is not a concern: the classifier is
a single VM with a single wake server.

## Permission model

The classifier SA holds **exactly** the `${enclave}_gpu_waker` custom role
on **exactly** one instance ID:

```hcl
# terraform/modules/iam/main.tf (excerpt — see issue #15)
resource "google_project_iam_custom_role" "gpu_waker" {
  permissions = [
    "compute.instances.get",
    "compute.instances.start",
  ]
}

resource "google_project_iam_member" "classifier_gpu_waker" {
  role   = google_project_iam_custom_role.gpu_waker.id
  member = "serviceAccount:${classifier_sa_email}"
  condition {
    expression = "resource.name == \"${gpu_instance_id}\""
  }
}
```

The wake helper enforces this contract from its side: it calls only `get`
and `start`, never `stop` or `delete`. A unit test
(`tests/test_integration.py::test_wake_only_uses_get_and_start_methods`)
asserts this is true at the call-site level. A second test cross-checks
that the IAM module's custom role grants exactly that permission set.

If the classifier SA's grant ever drifts, the wake helper returns
`permission_denied` immediately — it does not burn the timeout budget on a
permission failure.

## Configuration

The wake target is sourced from, in order:

1. **GCE instance metadata** (preferred). Terraform's classifier module
   writes:
   - `tabula-gpu-project-id`
   - `tabula-gpu-instance-zone`
   - `tabula-gpu-instance-name`
   - `tabula-gpu-health-url`
2. **`/etc/tabula/wake.json`** (fallback for local dev / tests).
   Override path with `$TABULA_WAKE_CONFIG`.

Every required field is loaded independently — partial metadata falls back
to the file per-field. Missing fields raise a clear error rather than
silently using a wrong target.

## Files

| Path                                    | Purpose                                     |
|-----------------------------------------|---------------------------------------------|
| `src/tabula_wake/wake_gpu.py`           | Core routine — state machine + idempotency  |
| `src/tabula_wake/config.py`             | Metadata + file config loader               |
| `src/tabula_wake/cli.py`                | `/opt/tabula/wake-gpu` CLI                  |
| `src/tabula_wake/server.py`             | `POST /wake` Flask app + `tabula-wake-server` |
| `systemd/tabula-wake.service`           | systemd unit for the wake server            |
| `tests/test_wake_gpu.py`                | Unit tests for the core routine             |
| `tests/test_config.py`                  | Config-loader unit tests                    |
| `tests/test_server.py`                  | Flask server tests via `test_client()`      |
| `tests/test_integration.py`             | End-to-end CLI + IAM contract tests         |

## Running tests

```bash
cd bootstrap/classifier/wake
python -m pytest -q
```

The test suite uses fakes only — no real GCP credentials, no real network,
no real `gcloud` binary. Flask is required for the server tests; if absent
those tests skip rather than fail.

## Failure modes (operational)

| symptom                                | likely cause                                    |
|----------------------------------------|-------------------------------------------------|
| `permission_denied` on every wake      | `gpu_waker` role unbound or `gpu_instance_id` mismatch in #15 |
| `timeout` with the VM stuck in `STAGING` | quota exhaustion or zonal capacity issue       |
| `timeout` with VM `RUNNING` but no health | GPU bootstrap (#35) regressed; check `/var/log/tabula-bootstrap.log` on the GPU VM |
| `api_error: backend exploded` etc.     | transient GCE outage; retry from the caller     |

## Non-goals

This issue intentionally does not implement:

- Auto-stop on idle — owned entirely by the GPU module's instance schedule
  (#19).
- Pub/Sub-based wake — the IAM trust boundary makes the direct call cleaner
  for MVP.
- Pre-warming based on time-of-day.
- Wake quotas / rate limiting — single-tenant enclave.
- Message buffering during cold-start — that is an L4 concern (Epic #13).
- Retry/backoff beyond the single configurable timeout.
- Multi-GPU coordination.
