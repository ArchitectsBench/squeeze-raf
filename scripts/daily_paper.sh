#!/usr/bin/env bash
# Daily paper forward-test: refresh data -> manage exits -> place -> track -> scan.
# Runs weekdays 16:35 ET via launchd (com.squeeze.daily-paper). Forward-test
# evidence only — nothing here is graduated.
#
# HARD RULE: if the data refresh fails, NOTHING is placed (stale data must never
# trade) and a high-priority ntfy alert fires. Every failed step alerts.
set -uo pipefail
cd "$(dirname "$0")/.."

TODAY=$(date +%Y-%m-%d)
LOG="logs/paper_${TODAY}.log"
PY=.venv/bin/python
NOTIFY=scripts/notify.sh
mkdir -p logs
FAILED=0

step() {  # step <name> <fatal:0|1> <cmd...>
  local name=$1 fatal=$2; shift 2
  if ! "$@" >> "$LOG" 2>&1; then
    echo "STEP FAILED: $name" >> "$LOG"
    bash "$NOTIFY" "daily_paper: step '$name' FAILED ($TODAY) — see $LOG" high
    FAILED=1
    if [ "$fatal" = 1 ]; then
      bash "$NOTIFY" "daily_paper: PLACEMENT ABORTED — data refresh failed ($TODAY)" urgent
      echo "=== aborted $(date +%H:%M:%S) ===" >> "$LOG"
      exit 1
    fi
  fi
}

echo "=== daily_paper $TODAY $(date +%H:%M:%S) ===" >> "$LOG"

# refresher-daemon heartbeat check (it feeds the screener; staleness = silent death)
HB=logs/refresher_heartbeat
if [ -f "$HB" ] && [ -n "$(find "$HB" -mmin +60 2>/dev/null)" ]; then
  bash "$NOTIFY" "refresher heartbeat STALE (>60min) — daemon likely dead" high
fi

# --- broad refresh: NON-fatal. It ingests 2h/4h/1d for the legacy squeeze/pullback
# strategies AND runs the strict integrity report over every TF — a thin ETF's bad
# 5min grid must not abort the prediction chain (it did, twice: 07-06, 07-07).
# On failure: alert + skip ONLY the legacy paper_trade placements.
BROAD_FAIL=0
if ! $PY -m data.report --ingest --universe broad --vendor polygon \
       --start 2021-08-01 --end "$TODAY" --force >> "$LOG" 2>&1; then
  echo "STEP FAILED: broad refresh (legacy placements will be skipped)" >> "$LOG"
  bash "$NOTIFY" "daily_paper: broad refresh FAILED — legacy paper_trade skipped; prediction chain continues" high
  BROAD_FAIL=1; FAILED=1
fi
# liquid-universe 1d — THE data the prediction chain and momentum rank on: FATAL.
step "liquid refresh" 1 $PY -m data.refresh_liquid --end "$TODAY"

# --- manage exits on EXISTING positions first, then place, then track ---
step "exit manager"   0 $PY -m reporting.exit_manager --place
if [ "$BROAD_FAIL" = 0 ]; then
  step "paper trade"  0 $PY -m reporting.paper_trade --place
fi
# live forward test (gates.yaml live_forward_test): snapshot every setup's verdict,
# then paper-trade the single top-conviction GO pick
step "bracket guard"  0 $PY -m reporting.bracket_guard --place
step "predictions"    0 $PY -m reporting.prediction_ledger
step "system pick"    0 $PY -m reporting.system_pick --place
# high-conviction GO -> trade card pushed to phone (for manual entry elsewhere)
step "trade alerts"   0 $PY -m reporting.trade_alert --push
# multiday timeframes (2d/3d/4d/1w) complete at the close — one card pass nightly
step "tf watch (multiday)" 0 $PY -m reporting.tf_watch --tfs 2d,3d,4d,1w --push
step "track"          0 $PY -m reporting.track

# EW-grade squeeze scan -> ntfy phone push (reads NTFY_TOPIC from .env)
step "squeeze scan"   0 $PY -m reporting.squeeze_scan --alerts

echo "=== done $(date +%H:%M:%S) rc=$FAILED ===" >> "$LOG"
exit $FAILED
