# `vertex-psc` — Vertex AI Private Service Connect endpoint

Terraform module that exposes Google APIs (including Vertex AI / `aiplatform.googleapis.com`) on a private internal IP inside the enclave VPC. With this module deployed, the GPU VM reaches Vertex AI **without** sending traffic through Cloud NAT to a public Google endpoint, which lets us drop Vertex AI / aiplatform CIDRs from the egress allowlist entirely.

This module pairs with the VPC module (`#14`) and is consumed implicitly by the GPU VM (`#19`).

## Architecture

```
                 ┌─ enclave VPC ─────────────────────────────────┐
                 │                                               │
   GPU VM ──────►│ aiplatform.googleapis.com                     │
   (no NAT route)│   ↳ private DNS zone resolves to PSC IP       │
                 │   ↳ PSC forwarding rule -> all-apis service   │
                 │      attachment (Google-managed)              │
                 │                                               │
                 └───────────────────────────────────────────────┘
                            │
                            ▼ (stays inside Google's network)
                  Vertex AI control plane
```

Two pieces make this work:

1. **PSC consumer endpoint** — a `google_compute_global_address` reserves an internal IP, and a `google_compute_global_forwarding_rule` with `load_balancing_scheme = ""` targets the Google-managed PSC service attachment for the chosen API bundle (`all-apis` by default).
2. **Private DNS override** — a `private` Cloud DNS zone for `googleapis.com.` shadows the public zone for VMs in this VPC, with an apex A record and a wildcard A record both pointing at the PSC IP. This way, `aiplatform.googleapis.com` and `<region>-aiplatform.googleapis.com` both resolve to the PSC IP from inside the VPC.

### Why `all-apis`?

Google publishes two PSC service attachments for the Private Google Access bundles:

- `all-apis` — every Google API the VPC's project has access to.
- `vpc-sc` — only services compatible with VPC Service Controls.

Vertex AI is in both bundles. We default to `all-apis` because the same endpoint also satisfies Logging, Monitoring, and Storage if those move off Private Google Access in the future. Set `psc_target = "vpc-sc"` if you want to lock the endpoint to the VPC-SC subset.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | (required) | GCP project hosting the VPC and the PSC endpoint. |
| `region` | string | (required) | Region used to construct the regional aiplatform hostname. |
| `enclave_name` | string | (required) | Short name used to prefix resource names (matches other modules). |
| `vpc_id` | string | (required) | Self-link/ID of the enclave VPC network. |
| `subnet_id` | string | (required) | Self-link/ID of the workload subnet (in `region`). Reserved for future use; the global PSC IP is not subnet-scoped. |
| `psc_target` | string | `"all-apis"` | PSC bundle to target: `all-apis` or `vpc-sc`. |
| `psc_address` | string | `null` | Optional explicit internal IP. Auto-allocated if null. |
| `create_dns_zone` | bool | `true` | Create the `googleapis.com.` private zone. Disable if a parent module manages a shared one. |

## Outputs

| Name | Description |
|---|---|
| `psc_ip` | Internal IPv4 of the PSC endpoint. |
| `psc_endpoint_name` | Name of the global forwarding rule. |
| `psc_address_self_link` | Self-link of the reserved IP. |
| `psc_dns_zone_name` | Private DNS zone name, or null if `create_dns_zone = false`. |
| `vertex_hostnames` | List of hostnames overridden to the PSC IP. |

## Verifying private connectivity

After `tofu apply` (or `terraform apply`), SSH into the GPU VM (or any VM in the VPC with no NAT route) and confirm Vertex AI is reachable **without** a public path:

```sh
# 1. Confirm DNS resolves to the PSC IP, not a public Google range.
dig +short aiplatform.googleapis.com
dig +short us-central1-aiplatform.googleapis.com
# Both should print the value of `psc_ip` (e.g., 10.10.0.5), not 142.x.x.x.

# 2. Confirm a Vertex AI control-plane call works end-to-end.
gcloud ai endpoints list --region "${REGION}"
# Should succeed even when Cloud NAT has NO allow rule for googleapis.com.

# 3. (Optional) Confirm the path is private with traceroute.
traceroute -n aiplatform.googleapis.com
# First hop should be the PSC IP, with no public hops.
```

If step 2 fails with a connection-timeout, the most common causes are:

- The GPU VM's service account lacks `roles/aiplatform.user` (granted by the IAM module, `#15`).
- `create_dns_zone = false` was set without provisioning an alternative private zone.
- The PSC forwarding rule's `load_balancing_scheme` is not `""` (Google rejects other values for API bundles).

## Coordination with other modules

- **VPC (`#14`)** — provides `vpc_id` and `subnet_id`. Once this module is in place, the egress allowlist in the VPC module can drop any Vertex AI / aiplatform CIDRs; that tightening should be done in a follow-up PR after both modules are merged.
- **IAM (`#15`)** — grants `roles/aiplatform.user` to the GPU service account. PSC handles the *network* path; IAM handles the *auth* path. Both are required.
- **GPU VM (`#19`)** — implicit consumer. The GPU VM uses the standard Vertex AI hostnames; this module makes those hostnames resolve privately.

## Non-goals

- PSC endpoints for individual non-Vertex APIs (Logging, Monitoring) — handled via Private Google Access on the subnet for MVP.
- Cross-region PSC — single region only.
- PSC consumer authentication — service-account auth is sufficient.
