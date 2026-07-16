#!/bin/sh
# Container entrypoint: wait for external Postgres, then exec the service command.
# Migrations and collectstatic are handled explicitly (compose command for dev;
# a dedicated one-off ECS task in production) — never implicitly on every start,
# so parallel tasks don't race.
set -e

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"

echo "Waiting for Postgres at ${DB_HOST}:${DB_PORT}..."
python - <<'PY'
import os, socket, time, sys
host = os.environ.get("DB_HOST", "db")
port = int(os.environ.get("DB_PORT", "5432"))
for _ in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            print("Postgres is up.")
            sys.exit(0)
    except OSError:
        time.sleep(1)
print("Postgres not reachable after 60s", file=sys.stderr)
sys.exit(1)
PY

exec "$@"
