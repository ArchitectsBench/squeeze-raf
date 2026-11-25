"""P8 options backtest runner (BUILD_SPEC.md §5 Stage 5).

    uv run python -m options_backtest.run --months 3

Reconstructs the REAL spread P&L (ORATS) for the graduated v1 (2h) signal's trades
in a recent window, then compares the realized SPREAD Sharpe to the UNDERLYING
signal Sharpe over the same matched trades. EOD options can't price intraday
round-trips, so trades that open and close the same day are skipped (logged).
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from backtest.engine import DEFAULT_TIME_STOP, ExitParams, SizingParams, simulate_symbol
from data.cache import ParquetCache
from data.universe import load_universe
from optimize.common import SignalCache
from signals import ConfluenceParams, generate
from spreads.dte import bucket_for_timeframe, dte_band
from spreads.iv_regime import iv_read

from .orats_adapter import OratsAdapter
from .spread_pnl import reconstruct, spread_metrics


def _per_trade_sharpe(returns: np.ndarray) -> float:
    sd = returns.std(ddof=1) if len(returns) > 1 else 0.0
    return float(returns.mean() / sd) if sd > 0 else 0.0


def main(argv: list[str] | None = None) -> int:
    pd.set_option("display.width", 200)
    ap = argparse.ArgumentParser(description="P8 options backtest")
    ap.add_argument("--tf", default="2h", choices=["2h", "4h", "1d"],
                    help="signal timeframe (higher TF = bigger moves, longer DTE)")
    ap.add_argument("--months", type=int, default=3, help="recent window to reconstruct")
    ap.add_argument("--max-trades", type=int, default=400)
    ap.add_argument("--dte-lo", type=int, default=None, help="override DTE band low")
    ap.add_argument("--dte-hi", type=int, default=None, help="override DTE band high")
    args = ap.parse_args(argv)
    tf = args.tf

    universe = load_universe("broad")
    cost_class = {s.ticker: s.cost_class for s in universe.symbols}
    cache = ParquetCache()
    frames = {s: cache.read(s, tf) for s in universe.tickers if cache.exists(s, tf)}
    frames = {s: d for s, d in frames.items() if d is not None and len(d) > 200}
    # 2h uses the graduated v1 (Mode A + 4h alignment); 4h/1d use base Mode A
    if tf == "2h":
        f4 = {s: cache.read(s, "4h") for s in universe.tickers if cache.exists(s, "4h")}
        sigcache = SignalCache(frames=frames, timeframe="2h", align_frames=f4,
                               conviction_min=0.0)

        def sig_for(sym, df):
            return sigcache.get(sym, length=13, confluence_window=4, sc_ready=1,
                                energy_lookback=6)
    else:
        def sig_for(sym, df):
            return generate(df, ConfluenceParams.for_timeframe(tf), mode="A")

    exits = ExitParams(time_stop_bars=DEFAULT_TIME_STOP[tf], atr_stop_mult=2.5,
                       profit_target_R=2.0, use_momentum_exit=True, use_profit_target=False)
    cutoff = max(d.index.max() for d in frames.values()) - pd.DateOffset(months=args.months)
    bucket = bucket_for_timeframe(tf)
    lo, hi = dte_band(bucket)
    if args.dte_lo is not None:
        lo, hi = args.dte_lo, (args.dte_hi or args.dte_lo + 7)
        print(f"(DTE override: targeting {lo}-{hi} DTE instead of the {bucket} bucket)")

    # collect underlying trades in the window
    pending = []
    for sym, df in frames.items():
        sig = sig_for(sym, df)
        _, trades = simulate_symbol(df, sig, cost_class.get(sym, "equities"), exits,
                                    SizingParams())
        daily = cache.read(sym, "1d")
        for t in trades:
            ets, xts = df.index[t["entry_bar"]], df.index[t["exit_bar"]]
            if ets < cutoff:
                continue
            if ets.normalize() == xts.normalize():
                pending.append(("SAMEDAY", None, None, None, None))
                continue
            regime = iv_read(daily["close"].loc[:ets])
            if regime is None:
                continue
            pending.append((sym, ets, xts, int(t["dir"]), float(t["pnl"]), regime.regime))

    real = [p for p in pending if p[0] != "SAMEDAY"]
    n_sameday = sum(1 for p in pending if p[0] == "SAMEDAY")
    real = real[: args.max_trades]
    print(f"=== P8 OPTIONS BACKTEST (ORATS) — {tf} signal, last {args.months} months "
          f"(bucket {bucket} -> {lo}-{hi} DTE) ===")
    print(f"underlying trades in window: {len(real)+n_sameday} "
          f"({n_sameday} same-day skipped — EOD options can't price intraday round-trips)")

    # prefetch ORATS days (cached parquet, universe-filtered)
    adapter = OratsAdapter(universe={s for s, *_ in real if s})
    dates = {p[1].normalize() for p in real} | {p[2].normalize() for p in real}
    print(f"fetching {len(dates)} ORATS trading days (cached after first pull)...")
    adapter.prefetch(dates)

    # reconstruct spreads, matched to underlying returns
    spread_trades, und_returns, missing = [], [], 0
    for sym, ets, xts, d, und_ret, regime in real:
        st = reconstruct(adapter, sym, ets.normalize(), xts.normalize(), d, regime, lo, hi)
        if st is None:
            missing += 1
            continue
        spread_trades.append(st)
        und_returns.append(und_ret)

    if not spread_trades:
        print(f"no reconstructable spreads ({missing} missing chains/legs).")
        return 1

    span_days = max((st.exit_date for st in spread_trades)) - min(st.entry_date for st in spread_trades)
    tpy = len(spread_trades) / max(span_days.days / 365.0, 1e-6)

    sm = spread_metrics(spread_trades, tpy)
    und = np.array(und_returns)
    und_pt_sharpe = _per_trade_sharpe(und)
    und_ann = und_pt_sharpe * np.sqrt(tpy)

    print(f"reconstructed {len(spread_trades)} spreads ({missing} unmatched). "
          f"~{tpy:.0f} trades/yr\n")
    print("--- REALIZED SPREAD vs UNDERLYING (matched trades) ---")
    tbl = pd.DataFrame([
        {"metric": "trades", "underlying": len(und), "spread": sm["n"]},
        {"metric": "win rate / POP", "underlying": round((und > 0).mean(), 3), "spread": round(sm["pop"], 3)},
        {"metric": "mean return-on-risk", "underlying": round(float(und.mean()), 4), "spread": round(sm["mean_return_on_risk"], 4)},
        {"metric": "per-trade Sharpe", "underlying": round(und_pt_sharpe, 3), "spread": round(sm["per_trade_sharpe"], 3)},
        {"metric": "annualized Sharpe", "underlying": round(und_ann, 2), "spread": round(sm["ann_sharpe"], 2)},
    ])
    print(tbl.to_string(index=False))
    print(f"\nspread best/worst trade (on risk): {sm['best']:.2f} / {sm['worst']:.2f}")
    print("\nExplanation: the spread Sharpe differs from the underlying because the "
          "vertical caps both upside and downside, pays the bid/ask + commission, and "
          "adds theta/vega exposure — the structure trades raw directional edge for a "
          "defined-risk, higher-POP payoff. Same-day trades are excluded (EOD data).")
    print("CAVEAT: structure (debit/credit) from the realized-vol regime; P&L from REAL "
          "ORATS prices/deltas. Signal is the borderline v1 (PBO 0.30).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
