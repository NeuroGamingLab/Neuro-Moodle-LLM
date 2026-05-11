variable "prefix" {
  description = "Prefix used for Azure resource names."
  type        = string
  default     = "neuro-moodle-llm"
}

variable "resource_group_name" {
  description = "Resource group name. If empty, uses {prefix}-rg."
  type        = string
  default     = ""
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "canadacentral"
}

variable "vm_size" {
  description = "VM SKU. Ollama + Moodle + builds need RAM; B2s is often too small."
  type        = string
  default     = "Standard_B4ms"
}

variable "admin_username" {
  description = "Linux admin user (SSH key auth only)."
  type        = string
  default     = "azureuser"
}

variable "ssh_public_key" {
  description = "SSH public key for the VM (e.g. contents of ~/.ssh/id_ed25519.pub)."
  type        = string
}

variable "allowed_cidr" {
  description = "CIDR allowed to reach SSH and published stack ports. Narrow for production."
  type        = string
  default     = "0.0.0.0/0"
}

variable "os_disk_size_gb" {
  description = "OS disk size (GB). Ollama images and Docker layers need headroom."
  type        = number
  default     = 256
}

variable "vnet_address_space" {
  type    = string
  default = "10.42.0.0/16"
}

variable "subnet_prefix" {
  type    = string
  default = "10.42.1.0/24"
}

variable "extra_tags" {
  type        = map(string)
  default     = {}
  description = "Extra resource tags."
}
