"""Naked long call/put backtest on the pullback signal (real ORATS data).

    uv run python -m options_backtest.long_run --months 9 --hold 5

Bullish pullback -> buy a ~ATM call; bearish -> buy a ~ATM put; hold `hold` trading
days; defined risk = premium, uncapped upside. Reports realized option win rate,
return-on-premium, Sharpe, profit factor, and the convex best/worst — the actual
test of whether the genuine short-term reversion edge monetizes as naked options.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from data.universe import load_universe
from signals.pullback import generate_pullback

from .long_option_pnl import long_option_metrics, reconstruct_long
from .orats_adapter import OratsAdapter


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    ap = argparse.ArgumentParser(description="naked long call/put backtest")
    ap.add_argument("--months", type=int, default=9)
    ap.add_argument("--hold", type=int, default=5, help="hold in trading days")
    ap.add_argument("--dte-lo", type=int, default=21)
    ap.add_argument("--dte-hi", type=int, default=45)
    ap.add_argument("--delta", type=float, default=0.50)
    ap.add_argument("--trend-ma", type=int, default=100)
    ap.add_argument("--oversold", type=float, default=15.0)
    ap.add_argument("--max-trades", type=int, default=500)
    args = ap.parse_args(argv)

    u = load_universe("broad")
    cache = ParquetCache()
    frames = {s: cache.read(s, "1d") for s in u.tickers if cache.exists(s, "1d")}
    frames = {s: d for s, d in frames.items() if d is not None and len(d) > 250}
    cutoff = max(d.index.max() for d in frames.values()) - pd.DateOffset(months=args.months)

    # collect pullback entries -> (sym, entry_date, exit_date=entry+hold, dir, und_ret)
    rows = []
    for sym, df in frames.items():
        sig = generate_pullback(df, trend_ma=args.trend_ma, oversold=args.oversold)
        idx = df.index
        for ts in sig.direction.index[sig.direction != 0]:
            i = idx.get_loc(ts)
            j = i + args.hold
            if idx[i] < cutoff or j >= len(idx):
                continue
            d = int(sig.direction.iloc[i])
            und = (df["close"].iloc[j] / df["close"].iloc[i] - 1.0) * d
            rows.append((sym, idx[i], idx[j], d, und))
    rows = rows[: args.max_trades]
    print(f"=== NAKED LONG CALL/PUT — pullback signal, last {args.months}mo, "
          f"hold {args.hold}d, ~{args.dte_lo}-{args.dte_hi} DTE, ~{args.delta} delta ===")
    print(f"signal entries in window: {len(rows)}")

    adapter = OratsAdapter(universe={r[0] for r in rows})
    dates = {r[1].normalize() for r in rows} | {r[2].normalize() for r in rows}
    print(f"fetching {len(dates)} ORATS days (cached after first pull)...")
    adapter.prefetch(dates)

    trades, und_ret, missing = [], [], 0
    for sym, e, x, d, und in rows:
        t = reconstruct_long(adapter, sym, e.normalize(), x.normalize(), d,
                             args.dte_lo, args.dte_hi, args.delta)
        if t is None:
            missing += 1
            continue
        trades.append(t)
        und_ret.append(und)
    if not trades:
        print(f"no reconstructable options ({missing} missing).")
        return 1

    span = max(t.exit_date for t in trades) - min(t.entry_date for t in trades)
    tpy = len(trades) / max(span.days / 365.0, 1e-6)
    m = long_option_metrics(trades, tpy)
    und = np.array(und_ret)

    print(f"reconstructed {len(trades)} option trades ({missing} unmatched). ~{tpy:.0f}/yr\n")
    print("--- REALIZED NAKED-OPTION PERFORMANCE ---")
    print(f"  win rate                : {m['win_rate']:.3f}")
    print(f"  mean return on premium  : {m['mean_return_on_premium']*100:+.1f}%  "
          f"(median {m['median_return']*100:+.1f}%)")
    print(f"  per-trade Sharpe        : {m['per_trade_sharpe']:.3f}")
    print(f"  annualized Sharpe       : {m['ann_sharpe']:.2f}")
    print(f"  profit factor           : {m['profit_factor']:.2f}")
    print(f"  best / worst trade      : {m['best']*100:+.0f}% / {m['worst']*100:+.0f}%")
    print(f"  underlying move (avg)   : {und.mean()*100:+.2f}% over {args.hold}d hold")
    print("\nConvex payoff: max loss = premium; a few big winners can carry many "
          "small premium losses. CAVEAT: structure from real ORATS quotes (buy ask/"
          "sell bid + commission); signal edge is short-term reversion (beta-beating "
          "at h=3-5 but modest). EOD entry/exit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
