# Tabula enclave network module

Foundation networking for the Tabula enclave. Creates:

- A single custom-mode VPC (`auto_create_subnetworks = false`) per enclave
- A single regional private subnet with Private Google Access enabled
- VPC flow logs (sampling configurable, default `0.5`)
- A Cloud Router + Cloud NAT for egress (no external IPs on workload VMs)
- An egress firewall allowlist with a low-priority deny-all backstop
- A `workload_network_tag` for downstream modules to tag VMs with

This module is intentionally narrow: it does not create IAM, service accounts,
ingress firewall rules, VMs, or PSC endpoints. Those live in sibling modules
(see parent epic in `tabula-project/tabula#12`).

## Trust boundary

The enclave's "no arbitrary internet egress" posture is enforced here. The
deny-all egress rule at priority `65000` catches anything not matched by an
allow rule at lower priority. Adding new egress destinations means adding a
rule at priority `< 65000`.

No `0.0.0.0/0` ingress is created by this module. Ingress (the Noise port) is
the firewall module's responsibility.

## Egress allowlist

| Priority | Direction | Destination                       | Ports | Purpose                                                  |
| -------- | --------- | --------------------------------- | ----- | -------------------------------------------------------- |
| 1000     | EGRESS    | `199.36.153.4/30`                 | 443   | `restricted.googleapis.com` (Vertex AI, GCS, AR)         |
| 1010     | EGRESS    | `199.36.153.8/30`                 | 443   | `private.googleapis.com` fallback                        |
| 1020     | EGRESS    | Google-mirrored bootstrap CIDRs   | 443   | apt / pypi / `packages.cloud.google.com`                 |
| 1100+    | EGRESS    | `var.extra_egress_cidrs[*].cidr`  | var   | Documented exceptions (one rule per entry)               |
| 65000    | EGRESS    | `0.0.0.0/0`                       | all   | Deny-all backstop                                        |

All allow rules above are scoped to VMs tagged with `${var.enclave_name}-workload`
(exported as `workload_network_tag`). VMs without the tag get no egress at all
through this module's rules — they fall through to GCP implicit defaults, but
those don't grant egress through Cloud NAT either since the NAT only routes
the workload subnet.

## Extending the allowlist

Two options, in order of preference:

1. Use Private Google Access. If the destination is a Google API, route it
   through `restricted.googleapis.com` or `private.googleapis.com`. No module
   change needed.
2. Add an entry to `var.extra_egress_cidrs`:

   ```hcl
   module "network" {
     source = "./modules/network"
     # ...
     extra_egress_cidrs = [
       {
         cidr        = "203.0.113.0/24"
         ports       = ["443"]
         description = "Vendor X webhook receiver — see ADR-NNN"
       },
     ]
   }
   ```

   Each entry creates its own firewall rule at priority `1100 + index` so
   audits can see them individually. Use `["all"]` for `ports` to allow all
   TCP, but document the justification carefully — every exception weakens
   the enclave's posture.

If neither option fits, edit `main.tf` to add a named rule at priority
strictly less than `65000`.

## Private Google Access trade-off

Workload VMs in this subnet have no external IPs. That means:

- DNS for `*.googleapis.com` must resolve to the VIP ranges (`199.36.153.4/30`
  for the restricted VIP, `199.36.153.8/30` for the private VIP). In a real
  deployment you typically pair this module with a private DNS zone for
  `googleapis.com` pointing at the VIPs. That zone is **not** created here —
  it lives with the consumer that needs it (see Gitea / GPU VM modules).
- Bootstrap traffic (apt, pypi, etc.) must go through Google-mirrored repos.
  Direct internet apt mirrors will be silently dropped by the deny-all rule.
- Anything that requires hitting a non-Google FQDN at install time needs an
  explicit `extra_egress_cidrs` entry — and someone has to look up the
  underlying CIDRs because GCP firewall rules are CIDR-based, not FQDN-based.

This is intentional: the enclave is supposed to be air-tight, and the
operational friction of a documented exception is the point.

## Inputs

| Name                | Type                                                  | Default          | Description                                                |
| ------------------- | ----------------------------------------------------- | ---------------- | ---------------------------------------------------------- |
| `project_id`        | `string`                                              | (required)       | GCP project ID hosting the enclave VPC                     |
| `region`            | `string`                                              | (required)       | GCP region for the regional subnet, router, and NAT        |
| `enclave_name`      | `string`                                              | (required)       | Short name used as a resource prefix and in the tag        |
| `subnet_cidr`       | `string`                                              | `"10.10.0.0/24"` | Primary IPv4 CIDR for the workload subnet                  |
| `nat_log_filter`    | `string`                                              | `"ERRORS_ONLY"`  | Cloud NAT log filter (`ERRORS_ONLY` / `TRANSLATIONS_ONLY` / `ALL`) |
| `flow_log_sampling` | `number`                                              | `0.5`            | VPC flow log sampling rate, `[0.0, 1.0]`                   |
| `extra_egress_cidrs`| `list(object({cidr, ports, description}))`            | `[]`             | Documented egress exceptions, one rule per entry           |

## Outputs

| Name                   | Description                                                          |
| ---------------------- | -------------------------------------------------------------------- |
| `vpc_id`               | ID of the enclave VPC                                                |
| `vpc_self_link`        | Self-link of the enclave VPC                                         |
| `vpc_name`             | Name of the enclave VPC                                              |
| `subnet_id`            | ID of the workload subnet                                            |
| `subnet_self_link`     | Self-link of the workload subnet                                     |
| `subnet_name`          | Name of the workload subnet                                          |
| `subnet_cidr`          | Primary IPv4 CIDR of the workload subnet                             |
| `nat_router_name`      | Name of the Cloud Router                                             |
| `nat_name`             | Name of the Cloud NAT gateway                                        |
| `region`               | Region the subnet/router/NAT live in                                 |
| `workload_network_tag` | Tag downstream modules MUST apply to workload VMs                    |

## Validation

```sh
cd terraform/modules/network/examples/basic
cp terraform.tfvars.example terraform.tfvars   # edit project_id
terraform init
terraform validate
terraform plan
```

`terraform validate` runs without GCP credentials. `terraform plan` requires
ADC pointing at a real project but does not mutate anything.

## Non-goals (handled elsewhere)

- Ingress firewall rules for the Noise port — separate issue
- Vertex AI Private Service Connect endpoint — separate issue
- VPC peering, shared VPC, multi-region — single-region MVP only
- Internal DNS zones — lives with the Gitea module (first consumer)
- IAM / service accounts — separate issue
- Remote state backend configuration — caller's responsibility
- Confidential VM / SEV-SNP networking concerns — parent epic non-goal
