#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  printf '%s\n' 'Missing root .env. Copy .env.example and add local model settings.' >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

for variable in MODEL_BASE_URL MODEL_API_KEY MODEL_NAME; do
  if [[ -z "${!variable:-}" ]]; then
    printf 'Missing required model setting: %s\n' "$variable" >&2
    exit 1
  fi
done

docker compose --env-file .env up --build -d postgres agent web

deadline=$((SECONDS + ${START_TIMEOUT_SECONDS:-180}))
for service in postgres agent web; do
  while :; do
    container_id="$(docker compose --env-file .env ps -q "$service")"
    status=""
    if [[ -n "$container_id" ]]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
    fi
    if [[ "$status" == healthy ]]; then
      break
    fi
    if (( SECONDS >= deadline )); then
      docker compose --env-file .env ps
      printf 'Timed out waiting for %s health\n' "$service" >&2
      exit 1
    fi
    sleep 2
  done
done

printf '%s\n' 'Web: http://localhost:3000' 'Agent API: http://localhost:2024'
