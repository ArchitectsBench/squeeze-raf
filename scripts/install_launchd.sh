#!/usr/bin/env bash
# Install/refresh the launchd jobs (daily paper run, refresher daemon, ledger backup).
# Idempotent: boots out any loaded copy first, then bootstraps the repo's plists.
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="$HOME/Library/LaunchAgents"
UID_N=$(id -u)
mkdir -p "$DEST" logs

FAILED=0
for plist in scripts/launchd/com.squeeze.*.plist; do
  label=$(basename "$plist" .plist)
  cp "$plist" "$DEST/"
  launchctl bootout "gui/$UID_N/$label" 2>/dev/null || true
  sleep 1   # bootout of a running (KeepAlive) job is async — immediate bootstrap races (error 5)
  if launchctl bootstrap "gui/$UID_N" "$DEST/$(basename "$plist")" 2>/dev/null; then
    echo "loaded $label"
  else
    sleep 3   # one retry after a longer settle
    if launchctl bootstrap "gui/$UID_N" "$DEST/$(basename "$plist")"; then
      echo "loaded $label (retry)"
    else
      echo "FAILED to load $label"; FAILED=1
    fi
  fi
done
[ "$FAILED" = 0 ] || exit 1
launchctl list | grep com.squeeze || true
