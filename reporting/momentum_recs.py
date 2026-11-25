"""Current residual-momentum recommendations (the best market-neutral book we have).

    uv run python -m reporting.momentum_recs            # top 10 long / 10 short
    uv run python -m reporting.momentum_recs --n 15 --gross 40000

Computes TODAY's residual (beta-neutral) momentum scores on the liquid universe,
sector-neutralizes, and returns a CONCENTRATED dollar-neutral long/short book: the
N highest-score names long, N lowest short, inverse-vol weighted to 50% gross each
side. NOTE: this signal is REAL (beats random-rank at the 99th pct) but did NOT pass
the graduation gauntlet on 5y of data (2023 momentum crash) — trade as forward-test
evidence / best-available market-neutral book, not a certified strategy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from signals.xsec_momentum import betas, resid_scores

UNIV = Path(__file__).resolve().parent.parent / "config" / "liquid_universe.json"
SECT = Path(__file__).resolve().parent.parent / "data" / "sectors.json"
CS = Path(__file__).resolve().parent.parent / "config" / "common_stocks.json"
LOOKBACK, SKIP = 252, 21      # chosen config
WEIGHT_CAP = 0.12             # max fraction of a leg in any single name (anti-concentration)


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    ap = argparse.ArgumentParser(description="Current residual-momentum L/S book")
    ap.add_argument("--n", type=int, default=10, help="names per side")
    ap.add_argument("--gross", type=float, default=20000.0, help="total gross $ (split 50/50)")
    ap.add_argument("--place", action="store_true", help="submit the book to the Tradier sandbox")
    args = ap.parse_args(argv)

    cache = ParquetCache()
    syms = json.loads(UNIV.read_text())
    sectors = json.loads(SECT.read_text()) if SECT.exists() else {}
    cs_only = set(json.loads(CS.read_text())) if CS.exists() else None   # Polygon type==CS
    closes = {s: cache.read(s, "1d")["close"] for s in syms if cache.exists(s, "1d")}
    C = pd.DataFrame({s: v for s, v in closes.items() if v is not None and len(v) > 300}).sort_index()
    RET = C.pct_change()
    mclose = cache.read("SPY", "1d")["close"].reindex(C.index).ffill()
    mret = mclose.pct_change()
    i = len(C) - 1
    asof = C.index[i].date()

    sc = resid_scores(C, RET, mret, mclose, i, LOOKBACK, SKIP).dropna()
    b = betas(RET, mret, i - LOOKBACK, i - SKIP)
    raw = (C.iloc[i - SKIP] / C.iloc[i - LOOKBACK] - 1.0)
    vol = RET.iloc[i - 60:i].std() * np.sqrt(252)

    # clean the universe: real common stocks only (have a SIC sector -> excludes ETFs/ETNs),
    # full lookback history, sane price, and drop split/data-artifact momentum outliers.
    full_hist = C.iloc[i - LOOKBACK].notna()
    clean = [s for s in sc.index
             if (cs_only is None or s in cs_only)         # Polygon type==CS (excludes ETFs/ETNs/trusts)
             and (not sectors or s in sectors)
             and bool(full_hist.get(s, False))
             and float(C.iloc[i][s]) >= 5.0
             and abs(float(raw.get(s, 0.0))) <= 3.0       # |12-1 ret| <= 300% (artifact guard)
             and abs(float(b.get(s, 0.0))) <= 3.0]        # no leveraged products
    dropped = len(sc) - len(clean)
    sc = sc.reindex(clean)
    if sectors:
        grp = pd.Series({s: sectors.get(s, "NA") for s in sc.index})
        sc = sc - sc.groupby(grp).transform("mean")        # sector-neutral

    ranked = sc.sort_values()
    longs, shorts = ranked.index[-args.n:][::-1], ranked.index[:args.n]
    half = args.gross / 2.0
    rows = []
    for side, names in (("LONG", longs), ("SHORT", shorts)):
        inv = (1.0 / vol.reindex(names)).replace([np.inf], np.nan).fillna(0.0)
        w = inv / inv.sum() if inv.sum() > 0 else pd.Series(1.0 / len(names), index=names)
        # cap any single name, then renormalize (anti-concentration, e.g. a stale-vol artifact)
        for _ in range(3):
            w = w.clip(upper=WEIGHT_CAP)
            w = w / w.sum()
        for s in names:
            px = float(C.iloc[i][s])
            notional = half * float(w[s]) * (1 if side == "LONG" else -1)
            rows.append({"side": side, "symbol": s, "resid_score%": round(float(sc[s]) * 100, 1),
                         "raw_12_1%": round(float(raw[s]) * 100, 1), "beta": round(float(b[s]), 2),
                         "vol_ann%": round(float(vol[s]) * 100, 1), "weight%": round(float(w[s]) * 50, 1),
                         "price": round(px, 2), "notional$": round(notional), "shares": int(notional / px)})

    df = pd.DataFrame(rows)
    print(f"=== RESIDUAL-MOMENTUM BOOK (sector-neutral, beta-neutral) — as of {asof} ===")
    print(f"universe {C.shape[1]} -> {len(sc)} clean (dropped {dropped}: ETFs/short-hist/penny/artifacts) "
          f"| {args.n} long / {args.n} short | gross ${args.gross:,.0f} | inverse-vol weighted\n")
    print(df.to_string(index=False))
    net = df["notional$"].sum()
    print(f"\nnet $ exposure: {net:+,.0f} (target ~0) | gross long ${half:,.0f} / short ${half:,.0f}")
    print("NOTE: REAL signal (random-rank beaten 99th pct) but NOT gauntlet-graduated — forward-test/best-available, not certified.")

    if args.place:
        from .paper_ledger import entries as _entries
        from .paper_ledger import record
        from .tradier import Tradier, TradierError
        tr = Tradier()
        if not tr.is_sandbox:
            print("\nREFUSING: TRADIER_BASE is not the sandbox. Aborting (paper only).")
            return 1
        already = {e["symbol"] for e in _entries() if e.get("source") == "momentum_ls"}
        print(f"\n--- PLACING dollar-neutral momentum book (sandbox {tr.account}) ---")
        placed = 0
        for _, r in df.iterrows():
            sym = r["symbol"]
            qty = abs(int(r["shares"]))
            if qty < 1 or sym in already:           # idempotent: don't double-place
                continue
            side = "buy" if r["side"] == "LONG" else "sell_short"
            try:
                resp = tr.equity_order(sym, side, qty, preview=False, otype="market")
                o = resp.get("order", resp)
                rec = {"kind": "entry", "ts": str(asof), "order_id": o.get("id"),
                       "symbol": sym, "asset": "equity", "side": side, "qty": qty,
                       "source": "momentum_ls", "hold_days": 21,
                       "conviction": round(abs(float(r["resid_score%"])) / 100, 2),
                       "ref_price": float(r["price"])}
                if o.get("id"):
                    record(rec)                      # record BEFORE anything else can fail
                    placed += 1
                print(f"  {sym:5} {side:10} x{qty:<4} -> {o.get('status', o)} (id {o.get('id')})")
            except TradierError as ex:
                print(f"  {sym:5} {side:10} -> ERROR {ex}")
        print(f"\n{placed} orders placed & recorded (source=momentum_ls, hold 21d). "
              "Scoped to OUR ledger only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
