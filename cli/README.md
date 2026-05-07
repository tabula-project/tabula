# tabula-cli

User-facing CLI for the Tabula enclave lifecycle.

## Status

* `tabula enclave down <name>` — implemented (issue #28).
* `tabula enclave up <name>` — in flight (issue #26). The state.json schema (`version: 1`) is shared; see [`_enclave_state.py`](src/tabula_cli/_enclave_state.py).
* `tabula enclave status <name>` — pending (issue #30).
* `tabula enclave ssh <name> {classifier|gpu|gitea}` — pending (issue #33).

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

## `tabula enclave ssh <name> {classifier|gpu|gitea}`

Opens an IAP-tunneled SSH session to a named enclave's classifier, GPU,
or Gitea VM. Reads `~/.tabula/enclaves/<name>/state.json` (written by
`tabula enclave up`) for the instance name, zone, and project ID, then
shells out to:

```
gcloud compute ssh <instance> \
    --zone=<zone> \
    --project=<project> \
    --tunnel-through-iap \
    [--ssh-flag=-A]            # only with --forward-agent
    [--command="..."]          # only with --command
```

### IAM prerequisites (operator must hold these BEFORE invoking)

The CLI does **not** auto-grant either of the following. If the operator
lacks them, `gcloud` will surface a clear 403; the CLI passes that error
through unchanged via exit-code propagation.

* `roles/iap.tunnelResourceAccessor` — to mint IAP tunnel tokens
* `roles/compute.osLogin` (or `roles/compute.instanceAdmin.v1`) — for
  OS Login key provisioning

### Examples

```sh
# Interactive shell into the classifier VM:
tabula enclave ssh demo classifier

# Run a single command, exit code propagated:
tabula enclave ssh demo gpu --command "nvidia-smi"

# Agent-forward so you can `git clone` from the in-enclave Gitea while
# sshed into the GPU VM:
tabula enclave ssh demo gpu --forward-agent

# Scriptable: do NOT prompt to start a STOPPED GPU; just fail.
tabula enclave ssh demo gpu --no-start --command "uptime"
```

### Exit codes

| code | meaning |
| ---: | --- |
| `0`  | clean SSH session (or `--command` ran successfully) |
| `1`  | user error (bad role/name, missing state, TERMINATED VM, gcloud missing) |
| `2`  | operator declined the GPU auto-start prompt |
| any other | propagated from the underlying `gcloud` invocation (e.g. `255` for SSH protocol error) |

### State VM lifecycle handling

| GCE status | behavior |
| --- | --- |
| `RUNNING` | open SSH session normally |
| `STOPPED` | warn + prompt to start (unless `--no-start`); declined -> exit `2` |
| `TERMINATED` | refuse and point at `tabula enclave up <name>` |

`STOPPED` is most common for the GPU VM (sleep schedule from #19); the
prompt path is reused uniformly for the other roles in case a human
manually stopped them.

## Future work (out of scope for this PR)

* Browser-based SSH (Cloud Shell SSH UI) — CLI only.
* Port-forwarding (e.g. `gcloud compute start-iap-tunnel` for Gitea HTTP).
* Multi-hop SSH (jump from classifier into GPU).
* SSH key management (relies entirely on OS Login defaults).
* Auto-granting `roles/iap.tunnelResourceAccessor` to the invoker.
