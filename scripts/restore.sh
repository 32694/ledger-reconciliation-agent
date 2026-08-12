#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  printf '%s\n' '用法：scripts/restore.sh [--yes] <备份文件>' >&2
}

assume_yes=false
case $# in
  1)
    if [[ "$1" == -* ]]; then
      usage
      exit 64
    fi
    backup_path="$1"
    ;;
  2)
    if [[ "$1" != "--yes" || -z "$2" || "$2" == -* ]]; then
      usage
      exit 64
    fi
    assume_yes=true
    backup_path="$2"
    ;;
  *)
    usage
    exit 64
    ;;
esac

if [[ ! -f "$backup_path" ]]; then
  printf '备份文件不存在或不是普通文件：%s\n' "$backup_path" >&2
  exit 66
fi

if [[ "$assume_yes" != true ]]; then
  printf '%s' '恢复将覆盖当前 Agent 数据库。请输入“确认恢复”以继续：'
  confirmation=''
  if ! IFS= read -r confirmation; then
    printf '%s\n' '未收到确认，已取消恢复。' >&2
    exit 1
  fi
  if [[ "$confirmation" != "确认恢复" ]]; then
    printf '%s\n' '确认文本不匹配，已取消恢复。' >&2
    exit 1
  fi
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  printf '%s\n' '缺少根目录 .env，无法执行恢复。' >&2
  exit 1
fi

compose=(docker compose --env-file "$ROOT_DIR/.env" -f "$ROOT_DIR/compose.yaml")
container_id="$("${compose[@]}" ps -q postgres 2>/dev/null)" || {
  printf '%s\n' '无法检查 postgres 健康状态。' >&2
  exit 1
}
if [[ -z "$container_id" ]]; then
  printf '%s\n' 'postgres 容器不存在，拒绝恢复。' >&2
  exit 1
fi
health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null)" || {
  printf '%s\n' '无法检查 postgres 健康状态。' >&2
  exit 1
}
if [[ "$health" != healthy ]]; then
  printf 'postgres 未就绪，拒绝恢复：%s\n' "$health" >&2
  exit 1
fi

if ! "${compose[@]}" exec -T postgres sh -c \
  'pg_restore --clean --if-exists --no-owner --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' <"$backup_path"; then
  printf '%s\n' '恢复失败，原备份文件保持不变。' >&2
  exit 1
fi

printf '%s\n' '恢复完成。'
