#!/usr/bin/env bash
# deploy.sh - pull latest and rebuild/restart the container.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

git restore .
git pull
docker compose up -d --build --force-recreate
