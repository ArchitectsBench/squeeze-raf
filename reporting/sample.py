"""Ad-hoc SAMPLE of what Mode A (the un-graduated 2h rule) emits — recent closed
trades + the freshest signals. This is NOT the P9 recommender (that's gated on
graduation); it's a concrete look at the entry/exit rules in action.

    uv run python -m reporting.sample

CAVEAT: Confluence v0 did NOT pass the P6 gauntlet (PBO 0.54, DSR 0). These are
illustrative, not tradeable recommendations.
"""

from __future__ import annotations

import sys

import pandas as pd

from backtest.engine import ExitParams, SizingParams, simulate_symbol
from data.cache import ParquetCache
from data.universe import load_universe
from indicators.ta import atr as atr_wilder
from signals import ConfluenceParams, generate

TF = "2h"
# graduated config (P5/P6 best by research Sharpe)
ENTRY = dict(confluence_window=4, sc_ready=1, energy_lookback=6)
EXITS = ExitParams(time_stop_bars=6, atr_stop_mult=2.5, profit_target_R=2.0,
                   use_momentum_exit=True, use_profit_target=False)
DTE_BUCKET = "2h bar -> 1-day hold -> 0-2 DTE"   # config holding_to_dte


def main(argv: list[str] | None = None) -> int:
    pd.set_option("display.width", 200)
    universe = load_universe("broad")
    cost_class = {s.ticker: s.cost_class for s in universe.symbols}
    cache = ParquetCache()

    closed, fresh = [], []
    for sym in universe.tickers:
        df = cache.read(sym, TF)
        if df is None or len(df) < 200:
            continue
        params = ConfluenceParams(length=13, **ENTRY)
        sig = generate(df, params, mode="A")
        atr = atr_wilder(df, 14)
        _, trades = simulate_symbol(df, sig, cost_class.get(sym, "equities"), EXITS,
                                    SizingParams())
        for t in trades:
            eb, xb = t["entry_bar"], t["exit_bar"]
            closed.append({
                "symbol": sym, "dir": "LONG" if t["dir"] > 0 else "SHORT",
                "entry_time": df.index[eb], "entry_px": round(t["entry_price"], 2),
                "exit_time": df.index[xb], "exit_px": round(t["exit_price"], 2),
                "bars_held": xb - eb, "return_%": round(t["pnl"] * 100, 2),
            })

        # freshest SIGNAL bars (entry signalled on close[t], would fill open[t+1]).
        # Show signals in the last ~15 bars as "current recommendations".
        recent = df.index[-15]
        sig_bars = sig.direction[(sig.direction != 0) & (sig.direction.index >= recent)]
        for ts, d in sig_bars.items():
            i = df.index.get_loc(ts)
            if i + 1 >= len(df):
                entry_px = None            # fills next bar — not printed yet
            else:
                entry_px = df["open"].iloc[i + 1]
            a = atr.iloc[i]
            stop = (entry_px - d * EXITS.atr_stop_mult * a) if entry_px else None
            fresh.append({
                "symbol": sym, "dir": "LONG" if d > 0 else "SHORT",
                "signal_close": ts, "fill≈next_open": round(entry_px, 2) if entry_px else "pending",
                "atr_stop": round(stop, 2) if stop else "-",
                "time_exit≈": df.index[min(i + 1 + EXITS.time_stop_bars, len(df) - 1)],
                "conviction": round(float(sig.conviction.loc[ts]), 2),
            })

    print("=" * 90)
    print("SAMPLE — Mode A @ 2h (Confluence v0).  *** UN-GRADUATED: illustrative only ***")
    print(f"entry={ENTRY} | exits: time={EXITS.time_stop_bars}b, "
          f"stop={EXITS.atr_stop_mult}xATR, momentum_exit={EXITS.use_momentum_exit}, "
          f"target={EXITS.use_profit_target}")
    print(f"holding/DTE mapping if structured into a spread (P7): {DTE_BUCKET}")
    print("=" * 90)

    cdf = pd.DataFrame(closed).sort_values("exit_time")
    print(f"\n--- 15 MOST RECENT CLOSED TRADES (of {len(cdf)} total) ---")
    print(cdf.tail(15).to_string(index=False))
    print(f"\n  quick stats over all {len(cdf)} trades: "
          f"win rate {round((cdf['return_%'] > 0).mean() * 100, 1)}%, "
          f"avg {round(cdf['return_%'].mean(), 3)}%, "
          f"median bars held {int(cdf['bars_held'].median())}")

    fdf = pd.DataFrame(fresh).sort_values("signal_close")
    print(f"\n--- FRESHEST SIGNALS (last ~15 bars to {cdf['exit_time'].max().date()}) "
          "= what the rule would 'recommend' now ---")
    if fdf.empty:
        print("  (no new signals in the most recent bars)")
    else:
        print(fdf.tail(20).to_string(index=False))
    print("\nNOTE: not tradeable — Confluence v0 failed P6 (PBO 0.54, DSR 0, OOS 1.13).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
