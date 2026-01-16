#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: ./run_migration.sh /path/to/inventory.db" >&2
  exit 1
fi

ENV_FILE=${ENV_FILE:-.env}

if [ ! -f "$ENV_FILE" ]; then
  GENERATED_PASSWORD=$(python - <<'PY'
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

if [ ! -d ".venv" ]; then
  python -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

pip install -r requirements.txt

python scripts/convert_sqlite_to_couchdb.py "$1"
