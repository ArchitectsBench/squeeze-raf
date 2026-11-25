"""Overnight ORATS archive materialization: cache universe-filtered daily parquets
for 2021-08..2024-12 (the span with equity 1d bars for setup detection), enabling
the FRESH-DATA re-test of the archive factors (F1 P/C alignment, F2 IV-rank) and the
GEX follow-up hypotheses on pre-2025 years.

    nohup .venv/bin/python scripts/materialize_orats.py >> logs/materialize_orats.log 2>&1 &

Idempotent (load_day skips cached days). Refreshes data/orats_factors.parquet at the
end so the factor cache covers the new days. Pushes ntfy on completion/failure.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from options_backtest.orats_adapter import OratsAdapter  # noqa: E402
from reporting.notify import push  # noqa: E402

START, END = "2021-08-02", "2024-12-31"


def main() -> int:
    syms = set(json.loads((ROOT / "config" / "common_stocks.json").read_text()))
    ad = OratsAdapter(universe=syms)
    dates = pd.bdate_range(START, END)
    t0, n, miss = time.time(), 0, 0
    try:
        for i, d in enumerate(dates):
            if ad.load_day(d) is not None:
                n += 1
            else:
                miss += 1
            if (i + 1) % 50 == 0:
                rate = (i + 1) / (time.time() - t0)
                eta_h = (len(dates) - i - 1) / rate / 3600
                print(f"{i+1}/{len(dates)} days ({n} data, {miss} missing) "
                      f"eta {eta_h:.1f}h", flush=True)
    except Exception as ex:
        push(f"ORATS materialization FAILED at day {n+miss}/{len(dates)}: {ex!r}",
             priority="urgent")
        raise
    # refresh the factor snapshot cache to cover the new days
    fact = ROOT / "data" / "orats_factors.parquet"
    if fact.is_file():
        fact.unlink()
    from validation.archive_factors_backtest import build_factor_cache  # noqa: E402
    fac = build_factor_cache()
    hours = (time.time() - t0) / 3600
    push(f"ORATS materialization DONE: {n} days cached ({miss} missing/holidays) in "
         f"{hours:.1f}h; factor cache rebuilt ({len(fac)} snapshots). "
         "Ready for the fresh-data F1/F2 re-test.", priority="default")
    print(f"DONE: {n} days, {miss} missing, {hours:.1f}h, factors {len(fac)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
