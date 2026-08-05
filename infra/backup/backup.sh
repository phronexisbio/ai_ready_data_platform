#!/bin/sh
# Nightly backup (BUILD_PLAN.md §10 Phase 10):
#   1. pg_dump the catalog database, uploaded to backups/postgres/
#   2. mc mirror the raw/ and features/ data lake zones into backups/mirror/
# `--no-owner --no-acl` on the dump: portable to a fresh Postgres instance
# that doesn't have the "catalog" role predefined (see restore verification).
set -eu

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
DUMP_FILE="/tmp/catalog-${TIMESTAMP}.sql"

echo "==> Dumping catalog database"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
  -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --no-owner --no-acl -F p > "$DUMP_FILE"
echo "    $(wc -l < "$DUMP_FILE") lines"

echo "==> Configuring mc"
mc alias set backup-target "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" > /dev/null
mc mb --ignore-existing backup-target/backups > /dev/null

echo "==> Uploading catalog dump"
mc cp "$DUMP_FILE" "backup-target/backups/postgres/catalog-${TIMESTAMP}.sql"

echo "==> Mirroring raw/ zone"
mc mirror --overwrite --quiet backup-target/raw backup-target/backups/mirror/raw

echo "==> Mirroring features/ zone"
mc mirror --overwrite --quiet backup-target/features backup-target/backups/mirror/features

echo "==> Backup complete: catalog-${TIMESTAMP}.sql"
