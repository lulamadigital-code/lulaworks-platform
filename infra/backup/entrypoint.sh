#!/bin/sh
# Scheduler loop for the backup container. Kept dependency-free (no cron daemon):
# run one backup on boot so a fresh deploy has an immediate baseline, then sleep
# until the next BACKUP_INTERVAL_SECONDS (default 24h) and repeat.
set -eu

: "${BACKUP_INTERVAL_SECONDS:=86400}"

# Invoke via `sh` so the job never depends on the mounted file's exec bit
# (bind-mounted scripts can arrive without +x on some hosts).
echo "[backup] scheduler up; interval=${BACKUP_INTERVAL_SECONDS}s"
while true; do
    sh /usr/local/bin/backup.sh || echo "[backup] FAILED at $(date -Iseconds)" >&2
    sleep "$BACKUP_INTERVAL_SECONDS"
done
