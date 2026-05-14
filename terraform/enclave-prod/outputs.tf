# Production composition outputs.
#
# The output NAMES (`classifier_ip`, `noise_port`, `enclave_name`) match
# the stub composition (`terraform/enclave/outputs.tf`) so the CLI's state
# parsing and `tabula enclave status` keep working unchanged across
# compositions.
#
# Semantic deviation:
#   - `classifier_ip` here is the classifier VM's INTERNAL IP. The classifier
#     is on a private subnet; operator access goes via IAP tunnel (see
#     `tabula enclave ssh`). External reachability for the Noise port is
#     handled by the firewall module's 0.0.0.0/0 allow rule, but the
#     classifier itself does not have a public IP.
#   - `noise_port` echoes the input variable. The actual listen port is
#     baked into the classifier VM's runtime config; this output is the
#     CLI's record of which port to dial.

output "classifier_ip" {
  description = "Internal IP of the classifier VM. Reachable via IAP tunnel or from within the VPC."
  value       = module.classifier.internal_ip
}

output "noise_port" {
  description = "TCP port the classifier listens on for Noise XX handshakes."
  value       = var.noise_port
}

output "enclave_name" {
  description = "Echo of the input name for convenience."
  value       = var.enclave_name
}
