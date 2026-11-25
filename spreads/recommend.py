"""Assemble a full spread recommendation from a directional signal (P7).

signal direction + IV regime -> structure -> strikes (delta + EM) -> DTE -> a rec
with structure, both strikes/deltas, net debit/credit, max P/L, breakeven, POP,
expectancy and IV-rank context.

    uv run python -m spreads.recommend        # recs for current v1 signals (2h)

CAVEAT: IV is a realized-vol PROXY and pricing is Black-Scholes (no options data
yet). The structuring LOGIC is what P7 validates (sane vs hand-checks); P8 swaps in
real IV/greeks and validates realized P&L. Underlying signal is the un-graduated v1.
"""

from __future__ import annotations

import sys

import pandas as pd

from data.cache import ParquetCache
from data.universe import load_universe
from indicators.ta import atr as atr_wilder
from optimize.common import SignalCache

from .dte import bucket_for_timeframe, dte_band, representative_dte
from .iv_regime import iv_read
from .strikes import build_spread
from .structure import choose

SIG_TF, ALIGN_TF = "2h", "4h"
GRAD_ENTRY = dict(length=13, confluence_window=4, sc_ready=1, energy_lookback=6)


def build_recommendation(symbol, direction, spot, daily_close, tf, atr_daily=None):
    iv = iv_read(daily_close)
    if iv is None:
        return None
    structure = choose(direction, iv.regime)
    bucket = bucket_for_timeframe(tf)
    lo, hi = dte_band(bucket)
    dte = representative_dte(bucket)
    spread = build_spread(spot, iv.iv, dte, structure,
                          atr=atr_daily, horizon_bars=dte)
    if spread is None:
        return None
    return {
        "symbol": symbol,
        "dir": "BULL" if direction > 0 else "BEAR",
        "iv_rank": iv.iv_rank, "regime": iv.regime,
        "structure": spread.structure + (" (mid/disc)" if structure.discretionary else ""),
        "DTE": f"{dte} ({lo}-{hi})",
        "long_K": spread.long_strike, "Lδ": spread.long_delta,
        "short_K": spread.short_strike, "Sδ": spread.short_delta,
        "net": (f"+{spread.net}" if spread.kind == "credit" else f"-{spread.net}"),
        "max_P": spread.max_profit, "max_L": spread.max_loss,
        "breakeven": spread.breakeven, "POP": spread.pop,
        "exp": spread.expectancy,
        "cr/w": spread.credit_pct_of_width if spread.credit_pct_of_width else "-",
        "EM": spread.expected_move,
    }


def main(argv: list[str] | None = None) -> int:
    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 40)
    universe = load_universe("broad")
    cache = ParquetCache()
    frames = {s: cache.read(s, SIG_TF) for s in universe.tickers if cache.exists(s, SIG_TF)}
    frames = {s: df for s, df in frames.items() if df is not None and len(df) > 200}
    align = {s: cache.read(s, ALIGN_TF) for s in universe.tickers if cache.exists(s, ALIGN_TF)}
    sigcache = SignalCache(frames=frames, timeframe=SIG_TF, align_frames=align,
                           conviction_min=0.0)

    recs = []
    for sym, df in frames.items():
        sig = sigcache.get(sym, **GRAD_ENTRY)
        recent = df.index[-10]
        live = sig.direction[(sig.direction != 0) & (sig.direction.index >= recent)]
        if live.empty:
            continue
        ts = live.index[-1]                 # freshest signal for this symbol
        d = int(live.iloc[-1])
        daily = cache.read(sym, "1d")
        if daily is None:
            continue
        spot = float(daily["close"].iloc[-1])
        atrd = float(atr_wilder(daily, 14).iloc[-1])
        rec = build_recommendation(sym, d, spot, daily["close"], SIG_TF, atr_daily=atrd)
        if rec:
            rec["signal"] = ts
            recs.append(rec)

    print("=" * 110)
    print("SPREAD RECOMMENDATIONS — v1 signal (2h) -> §6 structuring.  *** IV=realized-vol proxy, BS pricing; un-graduated ***")
    print("=" * 110)
    if not recs:
        print("(no current signals)")
        return 0
    cols = ["symbol", "dir", "signal", "iv_rank", "regime", "structure", "DTE",
            "long_K", "Lδ", "short_K", "Sδ", "net", "max_P", "max_L", "breakeven",
            "POP", "exp", "cr/w", "EM"]
    out = pd.DataFrame(recs)[cols].sort_values("signal")
    print(out.to_string(index=False))
    print("\nnet: -x = debit paid, +x = credit received. POP/exp from BS proxy. "
          "cr/w = credit as % of width (credit target 0.33-0.50).")
    print("NOTE: signal layer (v1) is borderline (PBO 0.30); spread layer needs real "
          "options data (P8) to validate P&L.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
