#!/bin/sh
# LulaWorks — restore a PostgreSQL backup produced by backup.sh.
#
# Usage (from the host):
#   docker compose -f docker-compose.prod.yml run --rm \
#     -e RESTORE_FILE=/backups/lulaworks_..._20260101_030000.sql.gz backup restore.sh
#
# Restores into the live DB. The dump was taken with --clean --if-exists, so it
# drops and recreates each object — existing data for those objects is replaced.
set -eu

: "${DB_HOST:=db}"
: "${DB_PORT:=5432}"
: "${DB_NAME:=lulaworks_platform}"
: "${DB_USER:=postgres}"
: "${RESTORE_FILE:?Set RESTORE_FILE=/backups/<dump>.sql.gz}"

[ -f "$RESTORE_FILE" ] || { echo "No such file: $RESTORE_FILE" >&2; exit 1; }
export PGPASSWORD="${DB_PASSWORD:?DB_PASSWORD must be set}"

echo "[restore] $(date -Iseconds) restoring ${RESTORE_FILE} → ${DB_NAME}"
gunzip -c "$RESTORE_FILE" \
    | psql --host="$DB_HOST" --port="$DB_PORT" \
           --username="$DB_USER" --dbname="$DB_NAME" -v ON_ERROR_STOP=1

echo "[restore] done. Run migrations if the dump predates the current code:"
echo "          docker compose -f docker-compose.prod.yml exec web python manage.py migrate"
