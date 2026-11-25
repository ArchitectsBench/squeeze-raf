#!/usr/bin/env bash
# Back up the trade ledger (the ONLY source of open-position truth in the shared
# account) + P&L snapshots. Runs hourly via launchd (com.squeeze.backup).
# Destination: iCloud Drive (syncs off-machine) if present, else ~/.squeeze-backups.
# Keeps a rolling "latest" copy plus one dated archive per day; prunes after 90 days.
set -euo pipefail
cd "$(dirname "$0")/.."

ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs"
if [ -d "$ICLOUD" ]; then
  DEST="$ICLOUD/squeeze-backups"
else
  DEST="$HOME/.squeeze-backups"
fi
mkdir -p "$DEST"

TODAY=$(date +%Y-%m-%d)
for f in data/paper_orders.jsonl data/paper_pnl.jsonl; do
  [ -f "$f" ] || continue
  base=$(basename "$f" .jsonl)
  cp "$f" "$DEST/${base}.latest.jsonl"
  # one immutable archive per day (first run of the day wins; later runs refresh it
  # only if the file grew — guards against restoring a truncated ledger over a good one)
  arch="$DEST/${base}.${TODAY}.jsonl"
  if [ ! -f "$arch" ] || [ "$(wc -c <"$f")" -gt "$(wc -c <"$arch")" ]; then
    cp "$f" "$arch"
  fi
done

find "$DEST" -name '*.jsonl' -not -name '*.latest.jsonl' -mtime +90 -delete
