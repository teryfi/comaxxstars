#!/bin/sh
set -eu

backup_dir="${BACKUP_DIR:-./backups/postgres}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"

case "$backup_dir" in
  ""|"/"|"~")
    echo "Unsafe BACKUP_DIR" >&2
    exit 2
    ;;
esac

umask 077
mkdir -p "$backup_dir"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/terstars-$timestamp.sql.gz"

docker compose --env-file .env.production exec -T postgres \
  pg_dump -U terstars_migrator -d terstars --no-owner --no-privileges | gzip -9 > "$target"
gzip -t "$target"
find "$backup_dir" -type f -name 'terstars-*.sql.gz' -mtime "+$retention_days" -delete
echo "Backup verified: $target"
