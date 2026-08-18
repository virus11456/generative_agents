#!/bin/bash
# Auto-update: pull the latest main branch and restart the Docker services
# whenever new commits land on GitHub. Progress is safe: the simulation
# auto-checkpoints and saves again on SIGTERM, and autorun resumes its
# lineage (my_town -> my_town-r2 -> ...) after every restart.
#
# Install once (checks every 5 minutes, logs to ~/auto_update.log):
#
#   chmod +x deploy/auto_update.sh
#   (crontab -l 2>/dev/null; echo "*/5 * * * * $HOME/generative_agents/deploy/auto_update.sh >> $HOME/auto_update.log 2>&1") | crontab -
#
set -e
cd "$(dirname "$0")/.."

git fetch origin main -q
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0
fi

echo "$(date '+%F %T') updating ${LOCAL:0:8} -> ${REMOTE:0:8}"
CHANGED=$(git diff --name-only "$LOCAL" "$REMOTE")
git pull -q origin main

# Restart the town only when backend-side files changed; frontend-only
# updates (templates, static, views) leave the simulation untouched, so
# routine UI updates never interrupt the world or fork a new -rN run.
if echo "$CHANGED" | grep -qE '^(reverie/|compose\.yaml|Dockerfile|requirements\.txt|\.env)'; then
  PROFILE=""
  if docker ps --format '{{.Names}}' | grep -q "autorun"; then
    PROFILE="--profile autorun"
  fi
  docker compose $PROFILE up -d --build --force-recreate
else
  docker compose up -d --build --force-recreate frontend
fi
echo "$(date '+%F %T') update complete"
