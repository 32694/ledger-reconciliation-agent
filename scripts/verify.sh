#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
mkdir -p artifacts
PYTHON_LOG="$ROOT_DIR/artifacts/python-verification.log"
FRONTEND_LOG="$ROOT_DIR/artifacts/frontend-verification.log"
: > "$PYTHON_LOG"
: > "$FRONTEND_LOG"

run_logged() {
  local log_file="$1"
  shift
  "$@" 2>&1 | tee -a "$log_file"
}

compose=(docker compose --project-name ledger-reconciliation-agent-verify -f "$ROOT_DIR/compose.yaml" --env-file /dev/null)
profiles=(--profile postgres-test --profile agent-verify --profile web-verify)

cleanup() {
  "${compose[@]}" "${profiles[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

run_logged "$PYTHON_LOG" "${compose[@]}" "${profiles[@]}" build postgres-test agent-verify web-verify
run_logged "$PYTHON_LOG" "${compose[@]}" "${profiles[@]}" up -d postgres-test agent-verify web-verify

deadline=$((SECONDS + ${VERIFY_TIMEOUT_SECONDS:-180}))
wait_for_health() {
  local service="$1"
  while :; do
    local container_id
    local status
    container_id="$("${compose[@]}" ps -q "$service" 2>/dev/null || true)"
    status=""
    if [[ -n "$container_id" ]]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
    fi
    if [[ "$status" == healthy ]]; then
      return 0
    fi
    if (( SECONDS >= deadline )); then
      run_logged "$PYTHON_LOG" "${compose[@]}" ps
      printf 'Timed out waiting for %s health\n' "$service" | tee -a "$PYTHON_LOG" >&2
      exit 1
    fi
    sleep 2
  done
}

for service in postgres-test agent-verify web-verify; do
  wait_for_health "$service"
done

run_logged "$PYTHON_LOG" "${compose[@]}" exec -T agent-verify python -m reconciliation_agent.cli migrate
run_logged "$PYTHON_LOG" "${compose[@]}" exec -T agent-verify python -m ruff check src tests
run_logged "$PYTHON_LOG" "${compose[@]}" exec -T agent-verify python -m mypy --strict src
run_logged "$PYTHON_LOG" "${compose[@]}" exec -T agent-verify python -m pytest -q
run_logged "$PYTHON_LOG" "${compose[@]}" exec -T agent-verify python -m reconciliation_agent.evaluation \
  --mode scripted --output /workspace/artifacts/evaluation.json
run_logged "$FRONTEND_LOG" "${compose[@]}" exec -T web-verify pnpm test
run_logged "$FRONTEND_LOG" "${compose[@]}" exec -T web-verify pnpm typecheck
run_logged "$FRONTEND_LOG" "${compose[@]}" exec -T web-verify pnpm build

printf '%s\n' 'Verification completed; see artifacts/evaluation.json.'
