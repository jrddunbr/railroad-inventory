#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR"

ENV_FILE=${ENV_FILE:-.env}
SYSTEM_PYTHON=${SYSTEM_PYTHON:-python3}
VENV_DIR=${VENV_DIR:-.venv}
VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"
VENV_FLASK="${VENV_DIR}/bin/flask"

if [ ! -f "$ENV_FILE" ]; then
  GENERATED_PASSWORD=$("$SYSTEM_PYTHON" - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
  )
  cat > "$ENV_FILE" <<EOF
COUCHDB_USER=admin
COUCHDB_PASSWORD=${GENERATED_PASSWORD}
COUCHDB_HOST=127.0.0.1
COUCHDB_PORT=5984
COUCHDB_DATABASE=model_inventory
EOF
  echo "Created ${ENV_FILE} with a generated CouchDB password."
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

COUCHDB_CONTAINER_NAME=${COUCHDB_CONTAINER_NAME:-modelinventory-couchdb}
COUCHDB_IMAGE=${COUCHDB_IMAGE:-docker.io/library/couchdb:3}
COUCHDB_USER=${COUCHDB_USER:-admin}
COUCHDB_PASSWORD=${COUCHDB_PASSWORD:-}
COUCHDB_PORT=${COUCHDB_PORT:-5984}
COUCHDB_HOST=${COUCHDB_HOST:-127.0.0.1}

mkdir -p data/couchdb

if ! command -v podman >/dev/null 2>&1; then
  echo "podman is required to run CouchDB." >&2
  exit 1
fi

if podman container exists "$COUCHDB_CONTAINER_NAME"; then
  if ! podman ps --format "{{.Names}}" | grep -q "^${COUCHDB_CONTAINER_NAME}$"; then
    podman start "$COUCHDB_CONTAINER_NAME" >/dev/null
  fi
else
  podman run -d \
    --name "$COUCHDB_CONTAINER_NAME" \
    -p "${COUCHDB_PORT}:5984" \
    -e "COUCHDB_USER=${COUCHDB_USER}" \
    -e "COUCHDB_PASSWORD=${COUCHDB_PASSWORD}" \
    -v "${PWD}/data/couchdb:/opt/couchdb/data:Z" \
    "$COUCHDB_IMAGE" >/dev/null
fi

export COUCHDB_USER
export COUCHDB_PASSWORD
export COUCHDB_HOST
export COUCHDB_PORT
export COUCHDB_URL=${COUCHDB_URL:-"http://${COUCHDB_USER}:${COUCHDB_PASSWORD}@${COUCHDB_HOST}:${COUCHDB_PORT}/"}

echo "Waiting for CouchDB to be ready..."
for _ in {1..30}; do
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS "${COUCHDB_URL}_up" >/dev/null 2>&1; then
      ready=1
      break
    fi
  else
    if "$SYSTEM_PYTHON" - <<PY >/dev/null 2>&1; then
import urllib.request
import os
url = os.environ.get("COUCHDB_URL", "").rstrip("/") + "/_up"
urllib.request.urlopen(url, timeout=2)
PY
      ready=1
      break
    fi
  fi
  sleep 1
done
if [ "${ready:-0}" -ne 1 ]; then
  echo "CouchDB did not become ready. Showing container logs:" >&2
  podman logs "$COUCHDB_CONTAINER_NAME" >&2 || true
  exit 1
fi

if [ ! -x "$VENV_PYTHON" ]; then
  "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
fi

"$VENV_PIP" install -r requirements.txt

"$VENV_FLASK" --app app run --debug
