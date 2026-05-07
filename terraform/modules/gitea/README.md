# Tabula enclave Gitea module

Stands up the private Gitea forge inside the enclave. Per Epic #12, Gitea is
the trust boundary for repo storage — the GPU VM clones from and pushes to it,
and GitHub is explicitly outside the enclave's trust surface.

This module creates:

- A `google_compute_instance` Gitea VM (no external IP), tagged
  `enclave-workload` and `enclave-gitea`.
- A separate `google_compute_disk` data disk attached at
  `/var/lib/gitea` with `auto_delete = false` (the standard GCP "survive a
  VM rebuild" pattern).
- Cloud-init that formats the data disk on first attach (idempotent), installs
  a pinned Gitea binary release, renders `/etc/gitea/app.ini` bound to the
  instance's internal IP, and runs Gitea under a systemd unit.
- A private `google_dns_managed_zone` for `<enclave_name>.internal` plus an
  `A` record `gitea.<enclave_name>.internal` -> instance internal IP.

## Trust boundary and scope

This module is the structural enforcement of:

- **No public IP on the Gitea VM** — internal-only, per Epic #12.
- **Gitea bound to the instance's internal IP**, never `0.0.0.0`. The
  cloud-init reads the instance metadata to discover the internal IP and
  writes it into `app.ini` as `HTTP_ADDR`.
- **Stable internal DNS name** for the GPU VM and other consumers, so they
  never have to track the Gitea instance's IP.

What this module does *not* do (handled elsewhere):

- Ingress firewall rules — the `enclave-gitea` network tag is the join point
  for #20/#24's firewall module.
- Gitea HA / replication.
- Backup of the data disk to GCS.
- SSH key sync between Gitea and the GPU VM (lives with Bootstrap, an Epic #12
  sub-issue).
- Gitea Actions / CI inside the enclave.
- TLS termination — internal-only HTTP is acceptable for MVP.
- Migrating existing repo content into the enclave (seeded by Bootstrap).

## Data disk persistence model

The data disk is created as a separate `google_compute_disk` resource and
attached via `attached_disk` with `auto_delete = false`. The semantics:

| Operation                                   | Data disk fate                             |
| ------------------------------------------- | ------------------------------------------ |
| In-place VM rebuild (machine-type change, image bump, etc.) | **Survives.** Reattached to the new VM. |
| Manual `gcloud compute instances delete`    | **Survives.** Disk is left orphaned, ready to reattach. |
| `terraform destroy` of this module          | **Destroyed**, alongside the instance.     |
| `terraform destroy` of the parent enclave   | **Destroyed**, alongside everything else.  |

This matches the issue's intent: "within an enclave's lifetime, the Gitea data
should survive a VM rebuild; across enclave teardowns, it's fine to lose it."

Cloud-init's first-boot script formats the disk as `ext4` only if it has no
filesystem (`blkid` returns nothing on a blank disk), so re-attaching an
existing data disk to a fresh VM preserves its contents. An `fstab` entry by
UUID is added so the mount survives reboot.

If you need to back the disk up before destroy, take a `google_compute_snapshot`
out-of-band before running `terraform destroy`.

## Internal DNS scheme

This module owns the `google_dns_managed_zone` for `<enclave_name>.internal`
because Gitea is the first internal-DNS consumer in the enclave.

- Zone visibility: `private` (resolvable only inside the attached VPC).
- Network attached: `var.network_self_link` (output `vpc_self_link` from
  network module #14).
- Record: `gitea.<enclave_name>.internal` `A` -> instance internal IP, TTL 300.

If a second module needs to add records to this zone later (e.g., a future
GPU-side service), the right move is to factor the zone out into a small
`dns/` module and have both Gitea and that module depend on its outputs.
That refactor is intentionally deferred until there is a second consumer.

## Gitea binary fetch — trust assumption

Cloud-init pulls the Gitea binary from `https://dl.gitea.com/gitea/<version>/gitea-<version>-linux-amd64`
on first boot. This implies:

- The Cloud NAT egress allowlist (#14) **must** permit `dl.gitea.com`. The
  network module's default allowlist does not include it, so an operator must
  add an `extra_egress_cidrs` entry, or set up a GCS-mirrored copy and patch
  the cloud-init URL.
- Trust in `dl.gitea.com`'s TLS certificate is the trust boundary. There is no
  signature pinning today. For a stronger posture, mirror the binary into a
  GCS bucket inside the project, switch the cloud-init URL to a `*.googleapis.com`
  destination (already allowed by the network module via Private Google
  Access), and add a SHA-256 verification step.

The `gitea_version` variable must be a full semver (e.g., `1.22.3`); a prefix
like `1.22` is rejected by validation, so pinned installs are reproducible.

## First-boot admin user

The cloud-init does **not** seed an admin user. There are two recommended
flows; pick the one that fits your operator workflow:

1. **Secret Manager + post-boot CLI** (recommended).
   - Create a Secret Manager entry holding the initial admin password
     out-of-band.
   - Grant the `gitea_sa_email` SA `roles/secretmanager.secretAccessor`
     conditioned to that single secret. Coordinate this binding in IAM
     module #15 to avoid a chicken-and-egg between this module and Secret
     Manager.
   - Run `gitea admin user create` once via SSH (OS Login) after the VM
     converges:

     ```sh
     gcloud compute ssh <instance> --zone <zone> -- \
       sudo -u gitea /usr/local/bin/gitea admin user create \
         --admin --username admin --random-password \
         --email admin@<enclave_name>.internal \
         --config /etc/gitea/app.ini
     ```

2. **Instance metadata fallback** (acceptable for ephemeral demo enclaves
   only). Push the password into instance metadata; have the cloud-init read
   `http://metadata.google.internal/.../attributes/<key>` and call
   `gitea admin user create`. Less clean: the password is now visible to
   anyone who can read project metadata.

The `app.ini` rendered by cloud-init sets `INSTALL_LOCK = true` and
`DISABLE_REGISTRATION = true`, so no external user can land on the install
wizard or self-register before the admin is seeded.

## Inputs

| Name                         | Type           | Default            | Description                                                                       |
| ---------------------------- | -------------- | ------------------ | --------------------------------------------------------------------------------- |
| `project_id`                 | `string`       | (required)         | GCP project hosting the enclave.                                                  |
| `region`                     | `string`       | (required)         | GCP region; must match the network module.                                        |
| `zone`                       | `string`       | (required)         | GCP zone for the VM and data disk.                                                |
| `enclave_name`               | `string`       | (required)         | Short prefix; forms the DNS suffix `<enclave_name>.internal`.                     |
| `network_self_link`          | `string`       | (required)         | VPC self-link (network module output `vpc_self_link`). Attached to the DNS zone.  |
| `subnet_self_link`           | `string`       | (required)         | Workload subnet self-link (network module output `subnet_self_link`).             |
| `gitea_sa_email`             | `string`       | (required)         | Email of the Gitea service account (IAM module output `gitea_sa_email`).          |
| `machine_type`               | `string`       | `"e2-small"`       | GCE machine type.                                                                 |
| `image_family`               | `string`       | `"ubuntu-2204-lts"`| Boot image family.                                                                |
| `image_project`              | `string`       | `"ubuntu-os-cloud"`| Boot image project.                                                               |
| `boot_disk_size_gb`          | `number`       | `20`               | Boot disk size.                                                                   |
| `boot_disk_type`             | `string`       | `"pd-balanced"`    | Boot disk type.                                                                   |
| `data_disk_size_gb`          | `number`       | `50`               | Persistent data disk size, mounted at `/var/lib/gitea`.                           |
| `data_disk_type`             | `string`       | `"pd-balanced"`    | Persistent data disk type.                                                        |
| `gitea_version`              | `string`       | `"1.22.3"`         | Pinned Gitea binary version (full semver).                                        |
| `extra_network_tags`         | `list(string)` | `[]`               | Extra GCE tags. Module always applies `enclave-workload` and `enclave-gitea`.     |
| `labels`                     | `map(string)`  | `{}`               | Resource labels merged with module defaults.                                      |
| `cloud_init_extra_user_data` | `string`       | `""`               | Optional cloud-init fragment appended after the module's runcmd.                  |

## Outputs

| Name                  | Description                                                                       |
| --------------------- | --------------------------------------------------------------------------------- |
| `instance_name`       | Name of the Gitea instance.                                                       |
| `instance_id`         | Full resource ID of the Gitea instance.                                           |
| `instance_self_link`  | Self-link of the Gitea instance.                                                  |
| `internal_ip`         | Primary internal IPv4 of the Gitea instance.                                      |
| `internal_fqdn`       | `gitea.<enclave_name>.internal` — what the GPU VM should reference.               |
| `data_disk_id`        | Resource ID of the persistent data disk.                                          |
| `data_disk_self_link` | Self-link of the persistent data disk.                                            |
| `zone`                | Zone the instance and data disk are pinned to.                                    |
| `dns_zone_name`       | Name of the `<enclave_name>.internal` private managed zone.                       |
| `dns_zone_dns_name`   | Trailing-dot DNS name of the private zone.                                        |
| `network_tags`        | Tags applied to the instance — for firewall composition.                          |

## Validation

The example under `examples/basic/` is a stub root module suitable for
`terraform validate` and `terraform plan` smoke tests:

```sh
cd terraform/modules/gitea/examples/basic
cp terraform.tfvars.example terraform.tfvars   # edit project_id
terraform init
terraform validate
terraform plan
```

`terraform validate` runs without GCP credentials. `terraform plan` requires
ADC pointing at a real project and the `compute` and `dns` APIs enabled, but
does not mutate anything.

## Dependencies

- **Hard:**
  - VPC module (#14) outputs: `vpc_self_link` -> `network_self_link`,
    `subnet_self_link` -> `subnet_self_link`. The network module's egress
    allowlist must permit `dl.gitea.com` (or a configured GCS mirror).
  - IAM module (#15) output: `gitea_sa_email`.
- **Consumed by:**
  - Bootstrap GPU startup — clones from `gitea.<enclave_name>.internal`.
  - Ingress firewall (#20 / #24) — keys off the `enclave-gitea` network tag.

## Non-goals (handled elsewhere)

- Gitea HA / replication.
- Disk-level backup to GCS (future work).
- SSH key sync between Gitea and the GPU VM (Bootstrap, Epic #12 sub-issue).
- Gitea Actions / CI inside the enclave.
- Migrating existing repo content (seeded by Bootstrap).
- TLS termination — plain HTTP over the private VPC is acceptable for MVP.
