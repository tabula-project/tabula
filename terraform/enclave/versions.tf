terraform {
  required_version = ">= 1.5"
  # No required_providers block: the stub composition needs no providers.
  # When real sibling modules land, they will declare google / google-beta
  # provider requirements; this block will gain a `required_providers`
  # entry then.
}
