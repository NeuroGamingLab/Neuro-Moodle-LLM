#!/usr/bin/env bash
# scripts/docker-run.sh
# Replace `docker compose` workflow with raw `docker build` + `docker run`.
# Idempotent: stops/removes existing project containers, rebuilds images, recreates network.
# Volumes are PRESERVED. To wipe persistent state too, run:
#   docker volume rm neuro_postgres_data neuro_moodledata neuro_qdrant_data neuro_ollama_data
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

NETWORK="neuro-net"
ENV_FILE="${REPO_ROOT}/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found at $ENV_FILE" >&2
  exit 1
fi

CONTAINERS=(neuro-streamlit neuro-moodle-llm moodle ollama qdrant postgres)
echo "==> Removing project containers (if present)"
for c in "${CONTAINERS[@]}"; do
  docker rm -f "$c" >/dev/null 2>&1 || true
done

echo "==> Ensuring network '$NETWORK' exists"
docker network inspect "$NETWORK" >/dev/null 2>&1 \
  || docker network create "$NETWORK" >/dev/null

echo "==> Building Moodle image"
docker build -t neuro-moodle-llm/moodle:local -f Dockerfile.moodle .

echo "==> Building Neuro ML API image"
docker build -t neuro-moodle-llm/api:local -f Dockerfile.neuro .

echo "==> Building Streamlit dashboard image"
docker build -t neuro-moodle-llm/streamlit:local -f Dockerfile.streamlit .

echo "==> Pulling base service images"
docker pull postgres:16.6-bookworm
docker pull qdrant/qdrant:v1.12.5
docker pull ollama/ollama:0.11.10

echo "==> Starting postgres"
docker run -d --name postgres \
  --network "$NETWORK" \
  --env-file "$ENV_FILE" \
  -p 5433:5432 \
  -v neuro_postgres_data:/var/lib/postgresql/data \
  --restart unless-stopped \
  --health-cmd 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  --health-interval 5s --health-timeout 5s --health-retries 20 --health-start-period 10s \
  postgres:16.6-bookworm >/dev/null

echo "==> Waiting for postgres to be healthy"
for i in {1..30}; do
  status=$(docker inspect -f '{{.State.Health.Status}}' postgres 2>/dev/null || echo starting)
  if [[ "$status" == "healthy" ]]; then break; fi
  sleep 2
done
[[ "$status" == "healthy" ]] || { echo "postgres did not become healthy"; exit 1; }

echo "==> Starting qdrant"
docker run -d --name qdrant \
  --network "$NETWORK" \
  -p 6333:6333 -p 6334:6334 \
  -v neuro_qdrant_data:/qdrant/storage \
  --restart unless-stopped \
  qdrant/qdrant:v1.12.5 >/dev/null

echo "==> Starting ollama"
docker run -d --name ollama \
  --network "$NETWORK" \
  -p 11434:11434 \
  -v neuro_ollama_data:/root/.ollama \
  --restart unless-stopped \
  ollama/ollama:0.11.10 >/dev/null

echo "==> Starting moodle (mounts local_neurollm plugin read-only)"
docker run -d --name moodle \
  --network "$NETWORK" \
  --env-file "$ENV_FILE" \
  -e MOODLE_DATAROOT=/var/moodledata \
  -e MOODLE_DB_HOST=postgres \
  -p 8080:80 \
  -v neuro_moodledata:/var/moodledata \
  -v "${REPO_ROOT}/moodle_plugins/neurollm:/var/www/html/public/local/neurollm" \
  --restart unless-stopped \
  neuro-moodle-llm/moodle:local >/dev/null

mkdir -p "${REPO_ROOT}/data"

echo "==> Starting neuro-moodle-llm (FastAPI)"
docker run -d --name neuro-moodle-llm \
  --network "$NETWORK" \
  --env-file "$ENV_FILE" \
  -e MOODLE_BASE_URL=http://moodle \
  -e MOODLE_HOST_HEADER=localhost:8080 \
  -e QDRANT_URL=http://qdrant:6333 \
  -e OLLAMA_HOST=http://ollama:11434 \
  -p 8888:8888 \
  -v "${REPO_ROOT}/data:/app/data" \
  --restart unless-stopped \
  neuro-moodle-llm/api:local >/dev/null

echo "==> Starting neuro-streamlit (operator dashboard)"
docker run -d --name neuro-streamlit \
  --network "$NETWORK" \
  --env-file "$ENV_FILE" \
  -e NEURO_API_BASE=http://neuro-moodle-llm:8888 \
  -e NEURO_DATA_DIR=/data \
  -e NEURO_DEFAULT_COURSE_ID=2 \
  -p 8501:8501 \
  -v "${REPO_ROOT}/data:/data:ro" \
  --restart unless-stopped \
  neuro-moodle-llm/streamlit:local >/dev/null

echo
echo "All containers up. Useful URLs:"
echo "  Moodle:        http://localhost:8080"
echo "  Neuro ML API:  http://localhost:8888/docs"
echo "  Streamlit:     http://localhost:8501"
echo "  Qdrant UI:     http://localhost:6333/dashboard"
echo "  Ollama:        http://localhost:11434"
echo
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
