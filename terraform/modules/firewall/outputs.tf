output "firewall_rule_names" {
  description = "Names of all firewall rules created by this module. Useful for downstream depends_on and teardown verification."
  value = compact([
    google_compute_firewall.allow_noise_ingress.name,
    length(google_compute_firewall.allow_iap_ssh) > 0 ? google_compute_firewall.allow_iap_ssh[0].name : "",
    google_compute_firewall.allow_classifier_to_gpu.name,
    google_compute_firewall.allow_gpu_to_gitea.name,
    google_compute_firewall.deny_all_ingress.name,
  ])
}

output "noise_ingress_rule_name" {
  description = "Name of the single external Noise allow rule. Exposed for assertions in tests and for downstream modules that need to depend_on it."
  value       = google_compute_firewall.allow_noise_ingress.name
}

output "deny_all_rule_name" {
  description = "Name of the explicit deny-all rule (priority 65534)."
  value       = google_compute_firewall.deny_all_ingress.name
}

output "iap_ssh_rule_name" {
  description = "Name of the IAP-SSH allow rule, or null if enable_iap_ssh = false."
  value       = length(google_compute_firewall.allow_iap_ssh) > 0 ? google_compute_firewall.allow_iap_ssh[0].name : null
}
