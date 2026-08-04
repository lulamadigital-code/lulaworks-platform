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

# ── Off-box copy to DigitalOcean Spaces / S3 (optional) ──────────────────────
# The droplet's local volume is not disaster recovery — a lost droplet loses it.
# If S3_BUCKET is set, push each dump to the bucket. aws-cli reads the
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION from the env.
# S3_ENDPOINT points aws-cli at Spaces (e.g. https://fra1.digitaloceanspaces.com);
# leave it unset for real AWS S3. Set S3_BUCKET empty to keep backups local only.
if [ -n "${S3_BUCKET:-}" ]; then
    key="s3://${S3_BUCKET}/${S3_PREFIX:-lulaworks-backups}/$(basename "$outfile")"
    echo "[backup] uploading → ${key}"
    if aws s3 cp "$outfile" "$key" ${S3_ENDPOINT:+--endpoint-url "$S3_ENDPOINT"}; then
        echo "[backup] off-box copy done"
    else
        echo "[backup] WARNING: off-box upload failed (local dump is still safe)" >&2
    fi
fi

# Retention: delete LOCAL dumps older than N days. (Remote retention is best
# handled by a bucket lifecycle policy — see docs/DEPLOYMENT.md.)
find "$BACKUP_DIR" -name 'lulaworks_*.sql.gz' -type f \
    -mtime "+${BACKUP_RETENTION_DAYS}" -print -delete || true

echo "[backup] done; keeping last ${BACKUP_RETENTION_DAYS} days locally"
