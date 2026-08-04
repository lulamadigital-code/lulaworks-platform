#!/bin/sh
# LulaWorks — nightly PostgreSQL backup.
#
# Runs inside the `backup` container (postgres:16-alpine, so pg_dump matches the
# server version). Dumps the whole database, gzips it to the backups volume, and
# prunes anything older than BACKUP_RETENTION_DAYS. A tiny, boring, reliable job.
set -eu

: "${DB_HOST:=db}"
: "${DB_PORT:=5432}"
: "${DB_NAME:=lulaworks_platform}"
: "${DB_USER:=postgres}"
: "${BACKUP_DIR:=/backups}"
: "${BACKUP_RETENTION_DAYS:=14}"

timestamp="$(date +%Y%m%d_%H%M%S)"
outfile="${BACKUP_DIR}/lulaworks_${DB_NAME}_${timestamp}.sql.gz"

mkdir -p "$BACKUP_DIR"
echo "[backup] $(date -Iseconds) → ${outfile}"

# PGPASSWORD is read from the environment (compose passes DB_PASSWORD through).
export PGPASSWORD="${DB_PASSWORD:?DB_PASSWORD must be set}"

# --clean --if-exists makes the dump safely restorable over an existing DB.
pg_dump \
    --host="$DB_HOST" --port="$DB_PORT" \
    --username="$DB_USER" --dbname="$DB_NAME" \
    --no-owner --no-privileges --clean --if-exists \
    | gzip -9 > "$outfile"

size="$(du -h "$outfile" | cut -f1)"
echo "[backup] wrote ${size}"

# Retention: delete dumps older than N days.
find "$BACKUP_DIR" -name 'lulaworks_*.sql.gz' -type f \
    -mtime "+${BACKUP_RETENTION_DAYS}" -print -delete || true

echo "[backup] done; keeping last ${BACKUP_RETENTION_DAYS} days"
