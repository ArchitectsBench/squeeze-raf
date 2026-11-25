#!/usr/bin/env bash
# Push an ops/failure alert to ntfy. Usage: notify.sh "message" [priority]
# Reads NTFY_TOPIC from .env at the repo root. Never fails the caller.
cd "$(dirname "$0")/.." || exit 0
TOPIC=$(grep -E '^NTFY_TOPIC=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
[ -z "$TOPIC" ] && exit 0
curl -fsS -m 10 -H "Title: squeeze-raf OPS" -H "Priority: ${2:-high}" \
     -d "$1" "https://ntfy.sh/$TOPIC" >/dev/null 2>&1 || true
