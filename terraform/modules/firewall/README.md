# Firewall module

GCP ingress firewall rules for the Tabula enclave. Composes with the VPC module
(which owns egress and Cloud NAT) to enforce the
"single Noise port externally, allowlisted egress, everything else denied"
posture from Epic #12.

This module is intentionally narrow: it produces only `google_compute_firewall`
resources. No IAM, no logging sinks, no NAT, no VPC.

## Trust boundary

External callers can reach exactly one TCP port: the classifier VM's Noise
listener. Everything else — Gitea, GPU services, internal APIs — is reachable
only from inside the VPC, and operator SSH is via Identity-Aware Proxy (IAP),
never via a public SSH port.

## Rule taxonomy

| Name                              | Direction | Source                | Target tag           | Protocol/port                | Priority | Action |
| --------------------------------- | --------- | --------------------- | -------------------- | ---------------------------- | -------- | ------ |
| `<prefix>-allow-noise-ingress`    | INGRESS   | `0.0.0.0/0`           | `enclave-classifier` | tcp/`var.noise_port` (7000)  | 1000     | allow  |
| `<prefix>-allow-iap-ssh`          | INGRESS   | `35.235.240.0/20`     | `enclave-workload`   | tcp/22                       | 1000     | allow  |
| `<prefix>-allow-classifier-to-gpu`| INGRESS   | tag `enclave-classifier` | `enclave-gpu`     | tcp/`var.wake_signal_port` (8088) | 1000 | allow  |
| `<prefix>-allow-gpu-to-gitea`     | INGRESS   | tag `enclave-gpu`     | `enclave-gitea`      | tcp/`var.gitea_port` (3000)  | 1000     | allow  |
| `<prefix>-deny-all-ingress`       | INGRESS   | `0.0.0.0/0`           | (any)                | all                          | 65534    | deny   |

Priority `1000` is GCP's default for allow rules. Priority `65534` matches
GCP's implicit-deny priority and is used here to make the deny-all explicit.

The `<prefix>` is configurable via `var.name_prefix` (default `enclave`) so
multiple enclaves can co-exist in one project without rule-name collisions.

### Why one resource per rule

We use one `google_compute_firewall` resource per rule (rather than `for_each`
over a map of rule definitions) because:

1. The rule set is small and stable.
2. Each rule's contract (source, target, port, semantics) is different —
   a single rendering template would hide more than it shares.
3. Per-resource diffs in `tofu plan` (or `terraform plan`) are easier to audit.

## IAP CIDR

The IAP TCP-forwarding service contacts VMs from `35.235.240.0/20`. This range
is well-known and stable. Source of truth:
<https://cloud.google.com/iap/docs/using-tcp-forwarding#before_you_begin>

If `var.enable_iap_ssh = false`, no SSH ingress is allowed at all and you must
reach the workloads via the serial console or by setting up SSH manually
through some other path.

## Tag contract

This module references the following network tags. The corresponding VM
modules are responsible for setting them:

| Tag                  | Set by         | Used by this module for             |
| -------------------- | -------------- | ----------------------------------- |
| `enclave-classifier` | classifier VM (#17) | target of Noise; source of wake-signal |
| `enclave-gpu`        | GPU VM (#19)        | target of wake-signal; source of Gitea |
| `enclave-gitea`      | Gitea VM (#21)      | target of Gitea HTTP                |
| `enclave-workload`   | all enclave VMs     | target of IAP-SSH                   |

The tags are referenced by name; this module plans cleanly even when the VM
modules do not exist yet. The tags simply match nothing until the VMs exist.

Tag names are configurable via the `*_tag` variables if you need to operate
multiple enclaves with different tag schemes in the same project.

## Adding a new internal service

When a new internal service (say, a metrics collector) is introduced:

1. Have its VM module set a fresh tag (e.g. `enclave-metrics`).
2. Add a new `google_compute_firewall` resource in `main.tf` with
   `source_tags` and `target_tags` for the producer/consumer pair.
3. Use the existing port-as-variable pattern. Default to a sensible port,
   make it overridable.
4. Update the rule-taxonomy table in this README.
5. Update `outputs.tf` so the new rule shows up in `firewall_rule_names`.

Do **not** add new `0.0.0.0/0` ingress rules. The whole point of the module
is that only one such rule exists.

## Inputs

See `variables.tf`. Required:

- `project_id`
- `network` (VPC self-link or name from the VPC module)

## Outputs

See `outputs.tf`. Notably:

- `firewall_rule_names` — all rule names, useful for `depends_on` and
  teardown verification.
- `noise_ingress_rule_name`, `deny_all_rule_name`, `iap_ssh_rule_name` —
  individual rule names for targeted assertions.

## Example

See `examples/basic/` for a minimal invocation.

## Future upgrade paths (out of scope for this module)

- **Cloud Armor** for L7 DDoS / WAF on the Noise port. Note: Cloud Armor
  attaches to load balancers, not raw VM firewall rules, so adopting it
  implies fronting the classifier with an LB.
- **Firewall logging** on individual rules. Off by default here to avoid
  log-volume surprises.
- **VPC Service Controls** for service-perimeter isolation around Vertex AI.
