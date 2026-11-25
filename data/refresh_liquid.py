"""Incremental 1d refresh of the FULL liquid universe (config/liquid_universe.json).

    uv run python -m data.refresh_liquid --end 2026-06-24

The momentum_ls forward-test ranks the whole liquid universe each day. The broad-universe
refresh in daily_paper.sh only covers ~160 names, so without this the momentum book silently
degrades to whatever subset happens to be current (~39 names). This pulls 1d for every liquid
name that isn't already current — cheap when most are up to date, full when stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from data.cache import ParquetCache
from data.polygon_adapter import PolygonAdapter

UNIV = Path(__file__).resolve().parent.parent / "config" / "liquid_universe.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="incremental refresh of the liquid universe")
    ap.add_argument("--end", required=True, help="end date YYYY-MM-DD")
    ap.add_argument("--start", default="2021-08-01")
    ap.add_argument("--tf", default="1d", help="timeframe (1d/30min/15min/2h/4h)")
    args = ap.parse_args(argv)

    syms = json.loads(UNIV.read_text())
    cache = ParquetCache()
    ad = PolygonAdapter()
    updated = current = err = 0
    for s in syms:
        cur = cache.read(s, args.tf)
        if cur is not None and len(cur) and str(cur.index[-1].date()) >= args.end:
            current += 1
            continue
        try:
            df = ad.fetch(s, args.tf, args.start, args.end)
            if df is not None and len(df) > 0:
                cache.write(s, args.tf, df, vendor="polygon")
                updated += 1
        except Exception:
            err += 1
    print(f"liquid {args.tf} refresh: updated {updated}, already-current {current}, "
          f"errors {err} (of {len(syms)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
