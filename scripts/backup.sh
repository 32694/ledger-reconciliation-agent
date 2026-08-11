#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if (( $# > 1 )); then
  printf '%s\n' '用法：scripts/backup.sh [输出文件]' >&2
  exit 64
fi

if (( $# == 1 )); then
  output_path="$1"
else
  output_path="$ROOT_DIR/backups/agent-$(date +%Y%m%d-%H%M%S).dump"
fi

if [[ -d "$output_path" ]]; then
  printf '备份输出目标不能是目录：%s\n' "$output_path" >&2
  exit 64
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  printf '%s\n' '缺少根目录 .env，无法执行备份。' >&2
  exit 1
fi

parent_dir="$(dirname -- "$output_path")"
mkdir -p -- "$parent_dir"
temporary_path="$(mktemp "$parent_dir/.agent-backup.XXXXXX")"

compose=(docker compose --env-file "$ROOT_DIR/.env" -f "$ROOT_DIR/compose.yaml")
if ! "${compose[@]}" exec -T postgres pg_dump --format=custom >"$temporary_path"; then
  printf '备份失败，已保留临时文件：%s\n' "$temporary_path" >&2
  exit 1
fi

mv -f -- "$temporary_path" "$output_path"
printf '备份已写入：%s\n' "$output_path"
