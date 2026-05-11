output "vm_name" {
  description = "Created Linux VM name."
  value       = azurerm_linux_virtual_machine.this.name
}

output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "public_ip_address" {
  description = "Public IPv4 for SSH and published services."
  value       = azurerm_public_ip.this.ip_address
}

output "ssh_connection" {
  value = format("ssh -i <path-to-private-key> %s@%s", var.admin_username, azurerm_public_ip.this.ip_address)
}

output "moodle_url" {
  value = format("http://%s:8080", azurerm_public_ip.this.ip_address)
}

output "neuro_api_url" {
  value = format("http://%s:8888/docs", azurerm_public_ip.this.ip_address)
}

output "streamlit_url" {
  value = format("http://%s:8501", azurerm_public_ip.this.ip_address)
}

output "qdrant_dashboard_url" {
  value = format("http://%s:6333/dashboard", azurerm_public_ip.this.ip_address)
}

output "post_deploy_hint" {
  description = "Set .env URLs to this public IP after rsync; then docker compose up."
  value       = <<-EOT
    1. Ensure repo root has moodle-latest-502.tar (see README.md) and a configured .env (from .env.example).

    2. From repo root: ./infra/azure/deploy.sh

    3. In .env on the VM (or before rsync), set at minimum:
         MOODLE_BASE_URL=http://${azurerm_public_ip.this.ip_address}:8080
         MOODLE_HOST_HEADER=${azurerm_public_ip.this.ip_address}:8080
         NEURO_API_CORS_ORIGINS=http://${azurerm_public_ip.this.ip_address}:8080
         QDRANT_URL / OLLAMA_HOST can stay compose-internal defaults for services; host-browser URLs use localhost in .env.example — adjust any browser-facing localhost URLs to this IP where needed.

    4. Pull Ollama models once the stack is healthy:
         docker compose exec ollama ollama pull llama3.2
         docker compose exec ollama ollama pull nomic-embed-text
  EOT
}
