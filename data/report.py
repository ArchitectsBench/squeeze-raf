"""Coverage report for the cached OHLCV universe + a small ingest CLI.

    # report what is already cached
    uv run python -m data.report

    # pull the pilot universe, then report
    uv run python -m data.report --ingest --vendor polygon \
        --start 2023-06-01 --end 2025-06-01

Shows rows per symbol per timeframe, date range, vendor, and the integrity
verdict so the P1 gate is auditable at a glance.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from .cache import ParquetCache
from .ingest import ingest_universe
from .schema import TIMEFRAMES, check_integrity
from .universe import Universe, load_universe


def coverage_table(cache: ParquetCache) -> pd.DataFrame:
    """One row per cached symbol/timeframe: rows, date range, vendor, integrity."""
    records = []
    for e in cache.entries():
        df = cache.read(e.symbol, e.timeframe)
        rep = check_integrity(df, e.symbol, e.timeframe)
        records.append(
            {
                "symbol": e.symbol,
                "timeframe": e.timeframe,
                "rows": e.rows,
                "start": e.start.date() if e.start is not None else None,
                "end": e.end.date() if e.end is not None else None,
                "vendor": e.vendor,
                "integrity": "OK" if rep.ok else f"FAIL ({len(rep.violations)})",
            }
        )
    if not records:
        return pd.DataFrame(
            columns=["symbol", "timeframe", "rows", "start", "end", "vendor", "integrity"]
        )
    df = pd.DataFrame(records)
    tf_order = {tf: i for i, tf in enumerate(TIMEFRAMES)}
    df["_o"] = df["timeframe"].map(tf_order).fillna(99)
    return df.sort_values(["symbol", "_o"]).drop(columns="_o").reset_index(drop=True)


def print_report(cache: ParquetCache, universe: Universe | None = None) -> bool:
    """Print the coverage table + a per-timeframe summary. Returns True if every
    cached frame passes integrity."""
    universe = universe or load_universe("pilot")
    table = coverage_table(cache)

    print("\n=== DATA COVERAGE REPORT ===")
    print(f"cache root : {cache.root}")
    print(f"universe   : {len(universe.tickers)} pilot symbols, "
          f"timeframes {list(universe.timeframes)}")

    if table.empty:
        print("\n(cache is empty — run with --ingest to pull data)\n")
        return True

    print()
    print(table.to_string(index=False))

    # pivot: rows per symbol x timeframe, plus expected-vs-present coverage
    pivot = table.pivot_table(
        index="symbol", columns="timeframe", values="rows", aggfunc="sum", fill_value=0
    )
    print("\n--- rows per symbol x timeframe ---")
    print(pivot.to_string())

    present = set(zip(table["symbol"], table["timeframe"]))
    expected = {(s, tf) for s in universe.tickers for tf in universe.timeframes}
    missing = sorted(expected - present)

    all_ok = (table["integrity"] == "OK").all()
    print("\n--- summary ---")
    print(f"frames cached      : {len(table)} / {len(expected)} expected")
    print(f"integrity          : {'ALL OK' if all_ok else 'FAILURES PRESENT'}")
    if missing:
        print(f"missing frames     : {len(missing)}")
        for s, tf in missing:
            print(f"    - {s} {tf}")
    print()
    return all_ok and not missing


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="OHLCV cache coverage report / ingest")
    p.add_argument("--ingest", action="store_true", help="pull the universe first")
    p.add_argument("--universe", default="pilot", help="named universe in config (pilot/broad/...)")
    p.add_argument("--vendor", choices=["polygon", "alpha_vantage"], default="polygon")
    p.add_argument("--start", default="2023-06-01")
    p.add_argument("--end", default="2025-06-01")
    p.add_argument("--force", action="store_true", help="re-fetch even if cached")
    args = p.parse_args(argv)

    cache = ParquetCache()
    universe = load_universe(args.universe)
    if args.ingest:
        print(f"Ingesting '{args.universe}' universe ({len(universe.tickers)} symbols) "
              f"from {args.vendor} [{args.start} .. {args.end}] ...")
        results = ingest_universe(
            args.vendor, args.start, args.end, universe=universe, cache=cache, force=args.force
        )
        for r in results:
            if r.error:
                print(f"  ! {r.symbol} {r.timeframe}: ERROR {r.error}")
            elif r.report and not r.report.ok:
                print(f"  ! {r.symbol} {r.timeframe}: INTEGRITY FAIL "
                      f"({len(r.report.violations)} issues, not cached)")

    ok = print_report(cache, universe)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
