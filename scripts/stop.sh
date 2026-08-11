#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  printf '%s\n' 'Missing root .env.' >&2
  exit 1
fi

compose=(docker compose --env-file .env -f "$ROOT_DIR/compose.yaml")
"${compose[@]}" stop postgres agent web
