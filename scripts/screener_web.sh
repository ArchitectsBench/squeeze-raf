#!/usr/bin/env bash
# Launch the squeeze-screener web app: FastAPI backend (:8000) + Next.js frontend (:3000).
# Add --refresh (or REFRESH=1) to ALSO start the Phase-2 data refresher daemon, which
# auto-updates the parquet cache per timeframe (config/refresh.yaml, RTH-aware).
#   bash scripts/screener_web.sh            # UI only (reads existing cache)
#   bash scripts/screener_web.sh --refresh  # UI + live data refresher
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PIDS=()
trap 'echo; echo "stopping…"; for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done' EXIT INT TERM

echo "starting backend  -> http://localhost:8000  (FastAPI)"
"$ROOT/.venv/bin/uvicorn" webapp.api.main:app --port 8000 --host 127.0.0.1 >> "$ROOT/logs/uvicorn.out" 2>&1 &
PIDS+=($!)

if [[ "${1:-}" == "--refresh" || "${REFRESH:-}" == "1" ]]; then
  echo "starting refresher (Phase 2 data daemon — config/refresh.yaml)"
  "$ROOT/.venv/bin/python" -m data.refresher &
  PIDS+=($!)
fi

echo "starting frontend -> http://localhost:3000  (Next.js)"
cd "$ROOT/webapp/frontend"
npm run dev
