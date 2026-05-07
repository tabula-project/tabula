# `terraform/enclave/stubs/` — placeholder sibling modules

These stubs let the root module `terraform plan` / `apply` succeed with no
GCP credentials so #26's `tabula enclave up <name> --dry-run` integration
test passes before the real sibling-module issues (#14, #15, #17, #19, #21,
#23, #24) merge.

Each stub:

- Declares the same input variables the real module is expected to accept.
- Returns synthetic outputs of the right shape and name.
- Uses no providers and creates no resources — only `output` and `locals`.

When the real sibling module merges, replace the corresponding
`source = "./stubs/<name>"` line in `../main.tf`. The CLI does not need to
change because the output names and types are part of the contract.

See `../README.md` for the full mapping table.
