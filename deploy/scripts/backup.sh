#!/usr/bin/env bash
# Postgres dump + blob store archive. Run from the server (cron: 0 3 * * *).
set -euo pipefail
OUT=${1:-$HOME/backups/ragb}
mkdir -p "$OUT"
stamp=$(date +%Y%m%d-%H%M%S)
docker exec ragb-postgres pg_dump -U "${POSTGRES_USER:-ragb}" -d "${POSTGRES_DB:-ragb}" \
  | gzip > "$OUT/db-$stamp.sql.gz"
docker run --rm -v ragb-seaweed:/src -v "$OUT":/out alpine \
  tar czf "/out/blobs-$stamp.tar.gz" -C /src .
# Two weeks of dailies is the recovery window this project promises; more is just disk.
find "$OUT" -name '*.gz' -mtime +14 -delete
echo "backup written to $OUT (db-$stamp, blobs-$stamp)"
