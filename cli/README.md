# tabula-cli

User-facing CLI for Tabula: enclave lifecycle and the `tabula servers` pubkey
pinning store.

## Status

* `tabula enclave up <name>` — implemented (issue #26).
* `tabula enclave down <name>` — implemented (issue #28). The state.json schema (`version: 1`) is shared; see [`state.py`](src/tabula_cli/state.py).
* `tabula enclave ssh <name> {classifier|gpu|gitea}` — implemented (issue #33).
* `tabula enclave status <name>` — implemented (issue #30).
* `tabula servers add/list/remove` — implemented (issue #32). Manages
  `~/.config/tabula/known_servers`. Persistence is delegated to
  `tabula_wire.client.pinning`.

## `tabula enclave up`

Provision (or re-apply) an enclave. Idempotent: re-running on an existing enclave prompts before re-applying.

```text
tabula enclave up <name> [--project=ID] [--region=R] [--dry-run] [--yes]
```

### Flags

| Flag | Effect |
|------|--------|
| `--project=ID` | GCP project ID. Defaults to `gcloud config get-value project`. |
| `--region=R`   | GCP region (default `us-central1`). |
| `--dry-run`    | Run `terraform plan` only; print plan and exit 0. |
| `--yes`        | Skip the re-apply confirmation prompt (CI / scripting). |
| `--verbose`    | Stream terraform stdout/stderr live. |

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Success. |
| `2`  | Validation error (bad name, missing project, etc.). |
| `3`  | Terraform invocation failed. |
| `4`  | GCP application-default credentials missing. |

## Install

```bash
pip install -e cli
# Or, with the optional GCP SDK extras for SDK-based verification:
pip install -e 'cli[gcp-sdk]'
```

## `tabula enclave down`

Tear an enclave down to zero residual cost. Idempotent and verification-driven.

```text
tabula enclave down <name> [--yes] [--force --project=PROJECT_ID]
```

### What it does

1. Reads `~/.tabula/enclaves/<name>/state.json`, validates `version == 1`.
2. Prompts for confirmation (warning that the **Gitea persistent disk and all repos will be lost**), unless `--yes`.
3. Runs `terraform destroy -auto-approve` in the enclave's terraform root.
4. **Independently verifies** via the GCP API that nothing labelled `enclave=<name>` remains. This is the line between "clean teardown" and "GPU running for a week" — terraform's exit code alone is not trusted.
5. On clean destroy + clean verify, removes `~/.tabula/enclaves/<name>/`.
6. On any failure, **leaves local state intact** so you can retry.

### Flags

| Flag | Effect |
|------|--------|
| `--yes` | Skip the destructive-action confirmation (CI/scripted teardown). |
| `--force` | Recovery path when `state.json` is missing or corrupt: queries GCP for resources labelled `enclave=<name>` and verifies cloud-side only. Requires `--project`. |
| `--project=ID` | Required with `--force`. The GCP project to query. |

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Clean destroy + clean verify (or clean idempotent no-op). |
| `1`  | User error (bad name, missing state without `--force`, etc.). |
| `2`  | `terraform destroy` failed; local state preserved. |
| `3`  | Post-destroy verification found leftover resources; local state preserved. |

### Recovery flow (`--force`)

If `state.json` is missing or unreadable but you know you have leftover GCP resources for an enclave:

```bash
tabula enclave down demo --force --project=tabula-demo-123
```

This skips terraform and queries `gcloud compute instances list --filter=labels.enclave=demo` directly. If anything is found, the command exits non-zero with the list of leftovers; you may need `gcloud compute instances delete` directly.

### Known follow-ups

* `tabula enclave down --all` — bulk teardown of every enclave under `~/.tabula/enclaves/`. Useful for demo cleanup; **not implemented** in this version.
* SDK-based (rather than `gcloud`-shell-out) verification path. Optional dependency `google-cloud-compute` is declared but not yet wired in.
* Stretch: also verify disks, addresses, firewall rules, and Cloud NAT. Today only GCE instances are verified — that is the floor in the issue acceptance.

## `tabula enclave status`

Read-only health diagnostic for an existing enclave. Reports per-VM state,
the GPU's "cold for N seconds", and the classifier's Noise-port reachability.
Never starts, stops, or modifies any cloud resource.

```text
tabula enclave status <name> [--json]
```

### What it does

1. Reads `~/.tabula/enclaves/<name>/state.json`, validates `version == 1`.
2. Probes each role VM (`classifier`, `gpu`, `gitea`) **concurrently** via
   `gcloud compute instances describe --format=json`. Authentication uses
   GCP Application Default Credentials transparently through `gcloud`.
3. TCP-connects to the classifier's Noise port (default 7777, override via
   `state.outputs.noise_port`) with a 2-second timeout.
4. Computes `cold_seconds` for a STOPPED GPU from its `lastStopTimestamp`.
5. Classifies health (a STOPPED GPU is **healthy** -- cold-by-default per
   Epic #12; a TERMINATED GPU is **unhealthy** -- preemption or crash).
6. Emits a human-readable summary, or a stable JSON document with `--json`.

### Flags

| Flag      | Effect                                                                      |
|-----------|-----------------------------------------------------------------------------|
| `--json`  | Emit a machine-readable JSON status document on stdout (see schema below). |

### Exit codes

| Code | Meaning                                                                  |
|------|--------------------------------------------------------------------------|
| `0`  | All expected VMs present and in expected state. `healthy: true`.         |
| `1`  | At least one VM is missing or unexpectedly `TERMINATED`. `healthy: false`. |
| `2`  | `state.json` not found, unreadable, or schema-incompatible.              |
| `3`  | GCP API error talking to `gcloud` (auth, quota, network).                |

### `--json` schema

```json
{
  "name": "demo",
  "project": "tabula-demo-123",
  "region": "us-central1",
  "created_at": "2026-05-07T12:34:56Z",
  "vms": [
    {
      "role": "classifier",
      "name": "demo-classifier",
      "state": "RUNNING",
      "zone": "us-central1-a",
      "internal_ip": "10.0.0.2",
      "external_ip": "34.x.x.x",
      "last_start": "2026-05-07T12:35:10Z",
      "last_stop": null
    },
    {
      "role": "gpu",
      "name": "demo-gpu",
      "state": "STOPPED",
      "zone": "us-central1-a",
      "internal_ip": "10.0.0.3",
      "external_ip": null,
      "last_start": "2026-05-07T13:01:02Z",
      "last_stop": "2026-05-07T13:14:30Z",
      "cold_seconds": 720
    }
  ],
  "reachability": {
    "noise_port": {"host": "34.x.x.x", "port": 7777, "reachable": true, "latency_ms": 41},
    "gitea": {"method": "iap", "reachable": false, "note": "internal only; no IAP probe wired"}
  },
  "healthy": true,
  "issues": []
}
```

`cold_seconds` is present only for a STOPPED GPU. `issues` is a list of
human-readable strings; `healthy` is `false` iff `issues` is non-empty.

### Read-only guarantee

The implementation calls only `gcloud compute instances describe` -- never
`start`, `stop`, `delete`, `reset`, or any `set-*`/`add-*`/`remove-*` verb.
This is enforced by a unit test that captures the argv of the real shell-out
path and asserts the verb whitelist.

## state.json schema (`version: 1`)

This is a contract shared with `tabula enclave up` (issue #26):

```json
{
  "version": 1,
  "name": "demo",
  "project_id": "tabula-demo-123",
  "region": "us-central1",
  "created_at": "2026-05-07T12:00:00Z",
  "terraform_dir": "/Users/x/.tabula/enclaves/demo/terraform",
  "outputs": { "classifier_ip": "1.2.3.4", "noise_port": 51820 }
}
```

Bumping the version requires explicit migration support. `down` rejects unknown versions with a clear error so a partially-upgraded toolchain cannot silently mishandle an existing enclave.

All enclave-owned cloud resources MUST carry the label `enclave=<name>`. The label is the only authoritative way to find leftovers when state is missing or corrupt; `down --force` relies on it exclusively.

## Tests

```bash
cd cli
pip install -e '.[test]'
pytest
```

Tests cover:

* Happy teardown (clean destroy + clean verification → state dir removed).
* Mocked-leftover detection (verifier returns a leftover instance → exit 3, state preserved).
* Idempotency (running `down` against an already-clean enclave is a no-op).
* Confirmation prompt path.
* Missing state path (without `--force` is a user error; with `--force` skips terraform).
* Terraform-failure path (state preserved, exit 2).
* `--force` recovery path.
* Schema version mismatch rejection.

## `tabula servers` (pubkey pinning store)

Manage the client-side server pubkey pinning store used by the chat dialer
(issue #32). The store lives at `~/.config/tabula/known_servers` and is the
single source of truth for "which static X25519 key do I trust for label
`alpha`?"

Tabula refuses to silently TOFU on first connect: pubkeys must be obtained
out-of-band and pinned before `tabula chat connect <label>` will succeed.

```text
tabula servers add <label> <host>:<port> <hex-pubkey> [--force]
tabula servers list
tabula servers remove <label>
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Success. |
| `1`  | Operational error (duplicate label without `--force`, unknown label on `remove`). |
| `2`  | User input error (malformed `host:port`, malformed hex pubkey, port out of range). |

### Notes

* The pubkey argument is a 64-character lowercase hex string — exactly what
  `tabula keygen` prints. Use the operator's published pubkey, not your own.
* The store file is created with `0o600` permissions and the parent dir with
  `0o700`. The loader rejects entries it cannot parse rather than silently
  dropping them.
* On-disk format and behavior are owned by `tabula_wire.client.pinning`; this
  CLI is a thin Typer wrapper.
