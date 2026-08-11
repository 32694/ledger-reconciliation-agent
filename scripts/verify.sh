#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
mkdir -p artifacts

compose=(docker compose --project-name ledger-reconciliation-agent-verify -f "$ROOT_DIR/compose.yaml" --env-file /dev/null)
profiles=(--profile postgres-test --profile agent-verify --profile web-verify)

cleanup() {
  "${compose[@]}" "${profiles[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" "${profiles[@]}" build postgres-test agent-verify web-verify
"${compose[@]}" "${profiles[@]}" up -d postgres-test agent-verify web-verify

deadline=$((SECONDS + ${VERIFY_TIMEOUT_SECONDS:-180}))
for service in postgres-test agent-verify web-verify; do
  until [[ "$("${compose[@]}" ps --format '{{.Service}} {{.Health}}' "$service" 2>/dev/null || true)" == *healthy* ]]; do
    if (( SECONDS >= deadline )); then
      "${compose[@]}" ps
      printf 'Timed out waiting for %s health\n' "$service" >&2
      exit 1
    fi
    sleep 2
  done
done

"${compose[@]}" exec -T agent-verify python -m reconciliation_agent.cli migrate
"${compose[@]}" exec -T agent-verify python -m ruff check src tests
"${compose[@]}" exec -T agent-verify python -m mypy --strict src
"${compose[@]}" exec -T agent-verify python -m pytest -q
"${compose[@]}" exec -T agent-verify python -m reconciliation_agent.evaluation \
  --mode scripted --output /workspace/artifacts/evaluation.json
"${compose[@]}" exec -T web-verify pnpm test
"${compose[@]}" exec -T web-verify pnpm typecheck
"${compose[@]}" exec -T web-verify pnpm build

printf '%s\n' 'Verification completed; see artifacts/evaluation.json.'
