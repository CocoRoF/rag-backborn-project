#!/usr/bin/env bash
set -euo pipefail

# Named volumes from an earlier release may still be root-owned. Repair once, then drop to
# an unprivileged tini as PID 1 — gosu is a one-way transition, so nothing regains root.
if [ "$(id -u)" -eq 0 ]; then
  mkdir -p /data /home/ragb/.claude /home/ragb/.cache
  marker=/data/.owner-uid-10001
  if [ ! -f "$marker" ]; then
    echo "[entrypoint] taking ownership of /data"
    chown -R 10001:10001 /data
    touch "$marker" && chown 10001:10001 "$marker"
  fi
  chown -R 10001:10001 /home/ragb/.claude /home/ragb/.cache
  exec gosu ragb /usr/bin/tini -- "$0" "$@"
fi

cd /app
case "${1:-api}" in
  api)
    echo "[entrypoint] alembic upgrade head"
    alembic upgrade head
    exec uvicorn ragb.main:app --host 0.0.0.0 --port 8000 --proxy-headers \
      --forwarded-allow-ips='*' --timeout-keep-alive 75
    ;;
  worker)
    # The API owns migrations. Waiting here keeps a cold start from racing the schema.
    attempt=0
    while [ "$attempt" -lt 90 ]; do
      alembic current 2>/dev/null | grep -q '(head)' && break
      attempt=$((attempt + 1)); sleep 2
    done
    alembic current 2>/dev/null | grep -q '(head)' || { echo "[entrypoint] schema never reached head" >&2; exit 1; }
    exec python -m ragb.worker
    ;;
  migrate) exec alembic upgrade head ;;
  shell)   exec bash ;;
  *)       exec "$@" ;;
esac
