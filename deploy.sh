#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"
DOCKER_CMD="${DOCKER_CMD:-docker}"
APP_PORT="${APP_PORT:-5000}"
COUCHDB_PORT="${COUCHDB_PORT:-5984}"
COMPOSE_FILE="${COMPOSE_FILE:-${ROOT_DIR}/docker-compose.yml}"

usage() {
  cat <<'EOF'
Usage: ./deploy.sh <command>

Commands:
  init      Create .env if missing and create persistent data directories.
  up        Build and start CouchDB, wait for readiness, then start the app.
  update    Back up CouchDB data, pull the latest git changes, rebuild, and restart.
  down      Stop the stack without deleting persistent data.
  logs      Follow container logs.
  status    Show running container status.

Environment overrides:
  ENV_FILE=/path/to/.env
  DOCKER_CMD=docker
  APP_PORT=5000
  COUCHDB_PORT=5984
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

compose() {
  "$DOCKER_CMD" compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

resolve_git_commit() {
  if command -v git >/dev/null 2>&1; then
    git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || true
  fi
}

generate_password() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
}

init_env() {
  if [ ! -f "$ENV_FILE" ]; then
    GENERATED_PASSWORD=$(generate_password)
    cat > "$ENV_FILE" <<EOF
COUCHDB_USER=admin
COUCHDB_PASSWORD=${GENERATED_PASSWORD}
APP_PORT=${APP_PORT}
COUCHDB_HOST=127.0.0.1
COUCHDB_PORT=${COUCHDB_PORT}
COUCHDB_DATABASE=model_inventory
SECRET_KEY=$(generate_password)
EOF
    echo "Created ${ENV_FILE}."
  fi
}

init_dirs() {
  mkdir -p \
    "${ROOT_DIR}/data/couchdb" \
    "${ROOT_DIR}/data/app" \
    "${ROOT_DIR}/data/uploads/railroad-logos" \
    "${ROOT_DIR}/data/backups/update"
}

backup_couchdb_data() {
  local backup_dir archive_path timestamp
  backup_dir="${ROOT_DIR}/data/backups/update"
  timestamp=$(date +%Y%m%d-%H%M%S)
  archive_path="${backup_dir}/couchdb-update-${timestamp}.tar.gz"

  if [ ! -d "${ROOT_DIR}/data/couchdb" ]; then
    echo "Skipping CouchDB backup because ${ROOT_DIR}/data/couchdb does not exist."
    return 0
  fi

  mkdir -p "$backup_dir"
  echo "Creating CouchDB backup at ${archive_path}..."
  tar -C "${ROOT_DIR}/data" -czf "$archive_path" couchdb
  echo "Created CouchDB backup at ${archive_path}."
}

wait_for_couchdb() {
  local user password url
  user=$(grep '^COUCHDB_USER=' "$ENV_FILE" | cut -d= -f2-)
  password=$(grep '^COUCHDB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
  url="http://${user}:${password}@127.0.0.1:${COUCHDB_PORT}/_up"

  echo "Waiting for CouchDB on port ${COUCHDB_PORT}..."
  for _ in {1..60}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "CouchDB did not become ready in time." >&2
  compose logs couchdb >&2 || true
  exit 1
}

cmd_init() {
  require_cmd "$DOCKER_CMD"
  require_cmd python3
  init_env
  init_dirs
}

cmd_up() {
  require_cmd "$DOCKER_CMD"
  require_cmd python3
  require_cmd curl
  APP_GIT_COMMIT=$(resolve_git_commit)
  export APP_GIT_COMMIT
  cmd_init
  compose up -d couchdb
  wait_for_couchdb
  compose up -d --build app
  compose ps
  echo "App should be available on http://127.0.0.1:${APP_PORT}/inventory"
}

cmd_update() {
  require_cmd git
  require_cmd tar
  require_cmd date
  require_cmd curl
  cmd_init
  compose down
  backup_couchdb_data
  git -C "$ROOT_DIR" pull --ff-only
  APP_GIT_COMMIT=$(resolve_git_commit)
  export APP_GIT_COMMIT
  compose up -d couchdb
  wait_for_couchdb
  compose up -d --build app
  compose ps
}

cmd_down() {
  require_cmd "$DOCKER_CMD"
  compose down
}

cmd_logs() {
  require_cmd "$DOCKER_CMD"
  compose logs -f
}

cmd_status() {
  require_cmd "$DOCKER_CMD"
  compose ps
}

COMMAND="${1:-}"

case "$COMMAND" in
  init)
    cmd_init
    ;;
  up)
    cmd_up
    ;;
  update)
    cmd_update
    ;;
  down)
    cmd_down
    ;;
  logs)
    cmd_logs
    ;;
  status)
    cmd_status
    ;;
  *)
    usage
    exit 1
    ;;
esac
