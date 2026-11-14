#!/usr/bin/env sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: $0 BACKEND_IMAGE FRONTEND_IMAGE" >&2
  exit 2
fi

ENV_FILE="${XUNTUI_ENV_FILE:-.env.production}"
export XUNTUI_BACKEND_IMAGE="$1"
export XUNTUI_FRONTEND_IMAGE="$2"

sh deploy/scripts/preflight.sh
docker compose --env-file "$ENV_FILE" -f compose.production.yml pull backend frontend
docker compose --env-file "$ENV_FILE" -f compose.production.yml up -d --no-build backend frontend
docker compose --env-file "$ENV_FILE" -f compose.production.yml ps
