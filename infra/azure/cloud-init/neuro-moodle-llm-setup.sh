#!/bin/bash
# Neuro-Moodle-LLM — Azure VM bootstrap: Docker Engine + Compose plugin, app layout.
# Terraform injects: admin_username

set -euo pipefail

LOG_FILE="/var/log/neuro-moodle-llm-setup.log"
APP_ROOT="/opt/neuro-moodle-llm"
REPO_DIR="$${APP_ROOT}/repo"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$${LOG_FILE}"
}

log "Starting Neuro-Moodle-LLM VM setup..."

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y

apt-get install -y \
  ca-certificates \
  curl \
  gnupg \
  lsb-release \
  git \
  rsync

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  >/etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable docker
systemctl start docker

usermod -aG docker "${admin_username}" || true

mkdir -p "$${REPO_DIR}" "$${REPO_DIR}/data"
chown -R "${admin_username}:${admin_username}" "$${APP_ROOT}"

cat >"$${APP_ROOT}/READ_ME_FIRST.txt" <<'EOF'
Neuro-Moodle-LLM — Azure VM
===========================

1. From your laptop (repo root), run:
     ./infra/azure/deploy.sh
   That rsyncs this repository to /opt/neuro-moodle-llm/repo/ and starts Docker Compose.

2. Prerequisites on the laptop before deploy:
   - .env configured (copy from .env.example); set MOODLE_BASE_URL and MOODLE_HOST_HEADER
     to http://<PUBLIC_IP>:8080 and <PUBLIC_IP>:8080 respectively.
   - moodle-latest-502.tar present at repo root (Dockerfile.moodle COPY).

3. On the VM after first boot:
     cd /opt/neuro-moodle-llm/repo
     sudo docker compose ps

4. Ollama models (once containers are healthy):
     sudo docker compose exec ollama ollama pull llama3.2
     sudo docker compose exec ollama ollama pull nomic-embed-text

5. Tighten Terraform variable allowed_cidr before production.
EOF
chown "${admin_username}:${admin_username}" "$${APP_ROOT}/READ_ME_FIRST.txt"

log "Docker + layout ready for ${admin_username} under $${APP_ROOT}"
log "Setup complete."
