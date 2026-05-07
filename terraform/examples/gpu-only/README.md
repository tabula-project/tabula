# `terraform/examples/gpu-only`

Minimal stub for plan-testing `terraform/modules/gpu` without the rest of the
enclave wiring. Used to satisfy the validation requirement in issue #19.

```sh
cd terraform/examples/gpu-only
terraform init
terraform validate
terraform plan
```

This driver passes literal placeholder values for the network and IAM inputs
that the GPU module would normally consume from the network module (#14) and
IAM module (#15). It is **not** suitable for `terraform apply` against a real
project — the placeholder VPC/subnet/SA do not exist. For an end-to-end smoke
test, see the manual smoke test section in `terraform/modules/gpu/README.md`.
