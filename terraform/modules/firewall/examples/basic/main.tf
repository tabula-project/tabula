# Minimal invocation of the firewall module.
#
# This example assumes the VPC module (#14) exists elsewhere; here we just
# pass a placeholder network name so `terraform validate` and
# `terraform plan` (with credentials) can exercise the module surface.

module "firewall" {
  source = "../../"

  project_id = var.project_id
  network    = var.network
}

output "firewall_rule_names" {
  value = module.firewall.firewall_rule_names
}

output "noise_ingress_rule_name" {
  value = module.firewall.noise_ingress_rule_name
}

output "deny_all_rule_name" {
  value = module.firewall.deny_all_rule_name
}
