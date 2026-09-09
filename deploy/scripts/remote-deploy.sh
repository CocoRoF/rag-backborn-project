#!/usr/bin/env bash
# Deploy to the production host: push, then pull + rebuild there.
#   ./scripts/remote-deploy.sh [service ...]
# With no arguments every service is rebuilt.
set -euo pipefail

HOST=${RAGB_HOST:-hrjang@116.47.69.209}
PORT=${RAGB_SSH_PORT:-2224}
DIR=${RAGB_DIR:-~/docker_web/rag-backborn}
SERVICES=("$@")

cd "$(dirname "$0")/../.."
if [ -n "$(git status --porcelain)" ]; then
  echo "working tree is dirty — commit first" >&2
  exit 1
fi
git push origin HEAD

# Two `compose up` runs at once collide on container names and take the stack down with
# them. One deploy at a time, enforced by a lock on the server.
ssh -p "$PORT" "$HOST" bash -se <<REMOTE
set -euo pipefail
cd $DIR
exec 9>.deploy.lock
flock -n 9 || { echo "another deploy is running"; exit 1; }
git fetch --all --prune
git reset --hard origin/main
cd deploy
docker compose -p ragb up -d --build ${SERVICES[*]:-}
docker compose -p ragb ps
REMOTE
