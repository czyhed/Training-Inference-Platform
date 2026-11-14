#!/usr/bin/env sh
set -eu

ENV_FILE="${XUNTUI_ENV_FILE:-.env.production}"
DATA_ROOT="${XUNTUI_DEPLOY_DATA_ROOT:-./deploy-data}"

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
docker compose version >/dev/null
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE; copy .env.production.example and inject secrets" >&2; exit 1; }

if grep -Eq 'replace-me|example\.com|postgres-host|minio-host' "$ENV_FILE"; then
  echo "$ENV_FILE still contains placeholders" >&2
  exit 1
fi

for directory in runtime artifacts datasets uploads model-cache training-work; do
  mkdir -p "$DATA_ROOT/$directory"
done

docker compose --env-file "$ENV_FILE" -f compose.production.yml config --quiet
printf 'preflight ok: env=%s data=%s\n' "$ENV_FILE" "$DATA_ROOT"
