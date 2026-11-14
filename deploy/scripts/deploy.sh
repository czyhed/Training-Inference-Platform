#!/usr/bin/env sh
set -eu

ENV_FILE="${XUNTUI_ENV_FILE:-.env.production}"
COMPOSE_FILE="${XUNTUI_COMPOSE_FILE:-compose.production.yml}"

sh deploy/scripts/preflight.sh
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

python - <<'PY'
import json, time, urllib.request
url = "http://127.0.0.1:8010/api/health"
for attempt in range(60):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            body = json.load(response)
        if body.get("status") == "ok" and body.get("storageBackend") == "postgres":
            print(json.dumps(body, ensure_ascii=False))
            break
    except Exception:
        pass
    time.sleep(2)
else:
    raise SystemExit("backend health check did not become ready")
PY
