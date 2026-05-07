output "classifier_ip" {
  description = "External IP of the classifier VM. Used by the CLI's `up` success summary."
  value       = module.classifier.external_ip
}

output "noise_port" {
  description = "Noise protocol UDP port the classifier listens on."
  value       = module.classifier.noise_port
}

output "enclave_name" {
  description = "Echo of the input name for convenience."
  value       = var.enclave_name
}
