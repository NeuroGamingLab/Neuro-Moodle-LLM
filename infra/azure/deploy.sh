#!/usr/bin/env bash
# Neuro-Moodle-LLM — Azure: terraform apply + rsync repo + docker compose up.
#
# Prerequisites: az login, terraform, rsync, SSH keypair; repo .env and moodle tarball for builds.
#
# Usage (repository root):
#   chmod +x infra/azure/deploy.sh
#   ./infra/azure/deploy.sh
#
# Optional env: LOCATION, VM_SIZE, ADMIN_USERNAME, SSH_KEY, SSH_PUB, PULL_OLLAMA_MODELS=1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TF_DIR="${SCRIPT_DIR}"
LOG_FILE="${TF_DIR}/deploy-azure.out"
SSH_KEY="${SSH_KEY:-${HOME}/.ssh/id_rsa}"
SSH_PUB="${SSH_PUB:-${HOME}/.ssh/id_rsa.pub}"
if [[ ! -f "${SSH_PUB}" && -f "${HOME}/.ssh/id_ed25519.pub" ]]; then
  SSH_PUB="${HOME}/.ssh/id_ed25519.pub"
  SSH_KEY="${HOME}/.ssh/id_ed25519"
fi

LOCATION="${LOCATION:-canadacentral}"
VM_SIZE="${VM_SIZE:-Standard_B4ms}"
ADMIN_USERNAME="${ADMIN_USERNAME:-azureuser}"
REPO_REMOTE="/opt/neuro-moodle-llm/repo"

echo "==== Neuro-Moodle-LLM — Azure deploy ====" | tee "${LOG_FILE}"
date | tee -a "${LOG_FILE}"
echo "PROJECT_ROOT=${PROJECT_ROOT}" | tee -a "${LOG_FILE}"

if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
  echo "ERROR: ${PROJECT_ROOT}/.env not found. Copy .env.example to .env and set secrets + MOODLE_BASE_URL for your public IP." | tee -a "${LOG_FILE}"
  exit 1
fi

if [[ ! -f "${SSH_KEY}" || ! -f "${SSH_PUB}" ]]; then
  echo "ERROR: SSH keypair not found. Set SSH_KEY / SSH_PUB (tried id_rsa and id_ed25519)." | tee -a "${LOG_FILE}"
  exit 1
fi

echo "[Step] Azure CLI session" | tee -a "${LOG_FILE}"
SUBSCRIPTION_ID="$(az account show --query id -o tsv 2>/dev/null || true)"
TENANT_ID="$(az account show --query tenantId -o tsv 2>/dev/null || true)"
if [[ -z "${SUBSCRIPTION_ID}" || -z "${TENANT_ID}" ]]; then
  echo "ERROR: az login required." | tee -a "${LOG_FILE}"
  exit 1
fi
export ARM_SUBSCRIPTION_ID="${SUBSCRIPTION_ID}"
export ARM_TENANT_ID="${TENANT_ID}"

cd "${TF_DIR}"

echo "[Step] terraform init" | tee -a "${LOG_FILE}"
terraform init -input=false 2>&1 | tee -a "${LOG_FILE}"

echo "[Step] terraform plan" | tee -a "${LOG_FILE}"
terraform plan \
  -var "ssh_public_key=$(cat "${SSH_PUB}")" \
  -var "location=${LOCATION}" \
  -var "vm_size=${VM_SIZE}" \
  -var "admin_username=${ADMIN_USERNAME}" \
  -out=tfplan 2>&1 | tee -a "${LOG_FILE}"

echo "[Step] terraform apply" | tee -a "${LOG_FILE}"
terraform apply -input=false tfplan 2>&1 | tee -a "${LOG_FILE}"

IP="$(terraform output -raw public_ip_address)"
if [[ -z "${IP}" ]]; then
  echo "ERROR: public_ip_address output empty." | tee -a "${LOG_FILE}"
  exit 1
fi

echo "[Step] Wait for SSH (${ADMIN_USERNAME}@${IP})" | tee -a "${LOG_FILE}"
SSH_OK=0
for _ in $(seq 1 24); do
  if ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no -o ConnectTimeout=12 "${ADMIN_USERNAME}@${IP}" 'echo ok' &>/dev/null; then
    SSH_OK=1
    break
  fi
  sleep 10
done
if [[ "${SSH_OK}" -ne 1 ]]; then
  echo "ERROR: SSH not ready." | tee -a "${LOG_FILE}"
  exit 1
fi

echo "[Step] Wait for cloud-init" | tee -a "${LOG_FILE}"
for _ in $(seq 1 36); do
  STATUS="$(ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no -o ConnectTimeout=12 \
    "${ADMIN_USERNAME}@${IP}" 'cloud-init status 2>/dev/null | sed -n "s/^status: //p"' || true)"
  echo "cloud-init status: ${STATUS:-unknown}" | tee -a "${LOG_FILE}"
  if [[ "${STATUS}" == "done" ]]; then
    break
  fi
  if [[ "${STATUS}" == "error" ]]; then
    echo "WARN: cloud-init reported error; continuing if Docker exists." | tee -a "${LOG_FILE}"
    break
  fi
  sleep 10
done

echo "[Step] rsync -> ${REPO_REMOTE}" | tee -a "${LOG_FILE}"
ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${ADMIN_USERNAME}@${IP}" "sudo mkdir -p '${REPO_REMOTE}' && sudo chown -R '${ADMIN_USERNAME}:${ADMIN_USERNAME}' /opt/neuro-moodle-llm"

COPYFILE_DISABLE=1 rsync -avz --delete-after -e "ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no" \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "DO-NOT-MODIFY" \
  --exclude "infra/azure/.terraform" \
  --exclude "infra/azure/*.tfstate" \
  --exclude "infra/azure/*.tfstate.*" \
  --exclude "infra/azure/tfplan" \
  --exclude "__pycache__" \
  --exclude ".mypy_cache" \
  --exclude ".pytest_cache" \
  --exclude ".DS_Store" \
  --exclude "*.pem" \
  --exclude "moodledata" \
  --exclude "postgres-data" \
  --exclude "qdrant_storage" \
  --exclude "ollama_data" \
  "${PROJECT_ROOT}/" "${ADMIN_USERNAME}@${IP}:${REPO_REMOTE}/" 2>&1 | tee -a "${LOG_FILE}"

echo "[Step] docker compose up (--build)" | tee -a "${LOG_FILE}"
ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${ADMIN_USERNAME}@${IP}" bash -s <<REMOTE
set -euo pipefail
cd '${REPO_REMOTE}'
sudo docker compose pull --ignore-pull-failures 2>/dev/null || true
sudo docker compose up -d --build
sudo docker compose ps
REMOTE

if [[ "${PULL_OLLAMA_MODELS:-}" == "1" ]]; then
  echo "[Step] Ollama model pulls (optional; can take a long time)" | tee -a "${LOG_FILE}"
  ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${ADMIN_USERNAME}@${IP}" bash -s <<'REMOTE' || true
set -e
cd /opt/neuro-moodle-llm/repo
sudo docker compose exec -T ollama ollama pull llama3.2
sudo docker compose exec -T ollama ollama pull nomic-embed-text
REMOTE
fi

echo | tee -a "${LOG_FILE}"
echo "Done. Moodle: http://${IP}:8080  |  API docs: http://${IP}:8888/docs  |  Streamlit: http://${IP}:8501" | tee -a "${LOG_FILE}"
echo "SSH: ssh -i ${SSH_KEY} ${ADMIN_USERNAME}@${IP}" | tee -a "${LOG_FILE}"
