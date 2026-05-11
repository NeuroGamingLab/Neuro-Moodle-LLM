# Azure VM (Terraform) — Neuro-Moodle-LLM

Provisions a single **Ubuntu 22.04** VM with Docker, opens ports for the **Docker Compose** stack (Moodle, Postgres, Qdrant, Ollama, FastAPI, Streamlit), and provides **`deploy.sh`** to sync this repo and run `docker compose up -d --build`.

Pattern is adapted from the internal reference under `DO-NOT-MODIFY/terraform-azure/azure/` (different workload); this tree is **committed** and specific to Neuro-Moodle-LLM.

## Prerequisites

- [Terraform](https://www.terraform.io/) `>= 1.5`, [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) (`az login`), `rsync`, OpenSSH client.
- An SSH key pair (defaults: `~/.ssh/id_rsa` or `~/.ssh/id_ed25519`).
- **Repository root** must have:
  - **`.env`** — from `.env.example`; before deploy, set URLs for the VM’s public IP (see below).
  - **`moodle-latest-502.tar`** — required by `Dockerfile.moodle` (see root `README.md`).

## One-shot: provision + sync + compose

From the **repository root**:

```bash
chmod +x infra/azure/deploy.sh
./infra/azure/deploy.sh
```

Optional environment:

| Variable | Default | Notes |
|----------|---------|--------|
| `LOCATION` | `canadacentral` | Azure region |
| `VM_SIZE` | `Standard_B4ms` | More RAM helps Ollama + builds |
| `ADMIN_USERNAME` | `azureuser` | Linux user |
| `SSH_KEY` / `SSH_PUB` | `~/.ssh/id_rsa` or ed25519 | Paths to private/public key |
| `allowed_cidr` | Terraform default `0.0.0.0/0` | Pass via `terraform apply -var` if you edit workflow |
| `PULL_OLLAMA_MODELS` | unset | Set to `1` to pull `llama3.2` and `nomic-embed-text` after compose (slow) |

## `.env` for the public IP

After the first `terraform apply`, note `public_ip_address` (or read it from `terraform output` before compose is healthy). At minimum align browser-facing values, for example:

```env
MOODLE_BASE_URL=http://<PUBLIC_IP>:8080
MOODLE_HOST_HEADER=<PUBLIC_IP>:8080
NEURO_API_CORS_ORIGINS=http://<PUBLIC_IP>:8080
```

Keep `MOODLE_DB_*`, `QDRANT_URL` / `OLLAMA_HOST` as in `.env.example` for **in-compose** service names unless you change networking.

## Manual Terraform (no rsync)

```bash
cd infra/azure
terraform init
terraform plan -var "ssh_public_key=$(cat ~/.ssh/id_ed25519.pub)" -out=tfplan
terraform apply tfplan
terraform output
```

Then SSH to the VM, install/copy the project under `/opt/neuro-moodle-llm/repo`, and run `sudo docker compose up -d --build` yourself. Cloud-init installs Docker; see `/opt/neuro-moodle-llm/READ_ME_FIRST.txt` on the VM.

## Security

- Restrict **`allowed_cidr`** in `variables.tf` or via `-var` to your office / VPN IP.
- **Postgres (5433)** is not opened on the NSG; DB stays reachable only inside Docker or via SSH tunnel.
- Put TLS and auth in front of Moodle/Streamlit for anything beyond lab use.

## Destroy

```bash
cd infra/azure
terraform destroy
```
