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

compose=(
  docker compose
  --project-name ledger-reconciliation-agent-verify
  -f "$ROOT_DIR/compose.yaml"
  --env-file /dev/null
  --profile postgres-test
  --profile agent-verify
  --profile web-verify
)

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

run_logged "$PYTHON_LOG" "${compose[@]}" build postgres-test agent-verify web-verify
run_logged "$PYTHON_LOG" "${compose[@]}" up -d --wait --wait-timeout "${VERIFY_TIMEOUT_SECONDS:-180}" \
  postgres-test agent-verify web-verify

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
