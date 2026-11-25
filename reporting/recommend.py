"""Multi-TF recommendation CLI (BUILD_SPEC.md §4; Phase P9).

Scans the universe across 2h/4h/1d, builds the §6 spread for each fresh signal,
applies the 4h+1d alignment bonus (raise conviction + prefer the longer-horizon
spread when they agree; down-rank on conflict), ranks, and writes a CSV + a
tearsheet. `--as-of DATE` reproduces recommendations for a fixed historical date
(causal: only bars <= DATE), deterministically. `--paper` dry-runs a Tradier
sandbox multileg order (gated on TRADIER_TOKEN/TRADIER_ACCOUNT; prints otherwise).

Signal layers: 2h uses the graduated v1 (Mode A + 4h alignment); 4h/1d use base
Mode A. CAVEAT: signal is borderline (PBO 0.30) and the spread layer uses an IV
proxy (P8 validates on real options data).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from data.cache import ParquetCache  # noqa: E402
from data.universe import load_universe  # noqa: E402
from indicators import raf, squeeze  # noqa: E402
from indicators.ta import atr as atr_wilder  # noqa: E402
from optimize.common import SignalCache  # noqa: E402
from signals import ConfluenceParams, generate  # noqa: E402
from spreads.dte import bucket_for_timeframe  # noqa: E402
from spreads.recommend import build_recommendation  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "tearsheets"
RECENT_BARS = 10                      # a signal is "live" if it fired within N bars
LENGTHS = {"2h": 13, "4h": 13, "1d": 21}
BUCKET_ORDER = ["1d", "2d", "3d", "5d"]


def regime_direction(df: pd.DataFrame, length: int) -> int:
    """Current higher-TF regime as a direction (+1/-1/0): ribbon + displaced-TEMA."""
    sq = squeeze.compute(df, length=length)
    rf = raf.compute(df)
    bull = bool(sq.ribbon.bullish.iloc[-1] and rf.savgstate.iloc[-1] >= 0)
    bear = bool(sq.ribbon.bearish.iloc[-1] and rf.savgstate.iloc[-1] <= 0)
    return 1 if bull else (-1 if bear else 0)


def _alignment(sig_dir: int, dir_4h: int, dir_1d: int) -> tuple[float, str]:
    """Bonus + tag from 4h/1d agreement with the signal direction."""
    agree = sum(1 for d in (dir_4h, dir_1d) if d == sig_dir)
    conflict = any(d == -sig_dir and d != 0 for d in (dir_4h, dir_1d))
    if agree == 2:
        return 0.20, "4h+1d aligned"
    if conflict:
        return -0.15, "HTF conflict"
    if agree == 1:
        return 0.10, "partial align"
    return 0.0, "neutral"


def _longer_bucket(bucket: str) -> str:
    i = BUCKET_ORDER.index(bucket)
    return BUCKET_ORDER[min(i + 1, len(BUCKET_ORDER) - 1)]


def scan(universe, cache, as_of: pd.Timestamp | None):
    # frames, optionally truncated to <= as_of (causal historical reproduction)
    def frames_for(tf):
        out = {}
        for s in universe.tickers:
            df = cache.read(s, tf)
            if df is None:
                continue
            if as_of is not None:
                df = df[df.index <= as_of]
            if len(df) > 200:
                out[s] = df
        return out

    f2, f4, f1 = frames_for("2h"), frames_for("4h"), frames_for("1d")
    v1cache = SignalCache(frames=f2, timeframe="2h", align_frames=f4, conviction_min=0.0)

    recs = []
    for sym in universe.tickers:
        if sym not in f2:
            continue
        daily = f1.get(sym)
        if daily is None:
            continue
        dir_4h = regime_direction(f4[sym], LENGTHS["4h"]) if sym in f4 else 0
        dir_1d = regime_direction(daily, LENGTHS["1d"])
        spot = float(daily["close"].iloc[-1])
        atrd = float(atr_wilder(daily, 14).iloc[-1])

        # candidate signals per timeframe
        candidates = []
        v1 = v1cache.get(sym, length=13, confluence_window=4, sc_ready=1, energy_lookback=6)
        candidates.append(("2h", v1))
        for tf, frames in (("4h", f4), ("1d", f1)):
            if sym in frames:
                candidates.append(
                    (tf, generate(frames[sym], ConfluenceParams.for_timeframe(tf), mode="A")))

        for tf, sig in candidates:
            recent = sig.direction.index[-RECENT_BARS]
            live = sig.direction[(sig.direction != 0) & (sig.direction.index >= recent)]
            if live.empty:
                continue
            ts, sdir = live.index[-1], int(live.iloc[-1])
            conv = float(sig.conviction.loc[ts])
            bonus, tag = _alignment(sdir, dir_4h, dir_1d)
            bucket = bucket_for_timeframe(tf)
            if tag == "4h+1d aligned":
                bucket = _longer_bucket(bucket)   # prefer the longer-horizon spread
            rec = build_recommendation(sym, sdir, spot, daily["close"], tf, atr_daily=atrd)
            if rec is None:
                continue
            rec.update(signal_tf=tf, signal_time=ts, holding_bucket=bucket,
                       conviction=round(conv, 3), align=tag,
                       adj_conviction=round(min(max(conv + bonus, 0.0), 1.2), 3))
            recs.append(rec)

    df = pd.DataFrame(recs)
    if df.empty:
        return df
    return df.sort_values("adj_conviction", ascending=False).reset_index(drop=True)


def tearsheet(df: pd.DataFrame, path: Path, title: str):
    top = df.head(15)
    fig, ax = plt.subplots(figsize=(10, max(3, 0.4 * len(top) + 1)))
    colors = ["seagreen" if d == "BULL" else "indianred" for d in top["dir"]]
    ax.barh(range(len(top)), top["adj_conviction"], color=colors)
    ax.set_yticks(range(len(top)),
                  [f"{r.symbol} {r.signal_tf} {r.structure.split('_')[0]}" for r in top.itertuples()])
    ax.invert_yaxis()
    ax.set_xlabel("adjusted conviction (align bonus applied)")
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def paper_submit(rec: dict) -> str:
    """Dry-run a Tradier sandbox multileg order. Submits only if TRADIER_TOKEN and
    TRADIER_ACCOUNT are set; otherwise returns the order it WOULD place."""
    leg = (f"{rec['symbol']} {rec['structure']} "
           f"long {rec['long_K']}/short {rec['short_K']} x1 ({rec['DTE']})")
    token = os.environ.get("TRADIER_TOKEN")
    acct = os.environ.get("TRADIER_ACCOUNT")
    if not token or not acct:
        return f"[DRY-RUN] would submit: {leg}"
    import httpx
    base = os.environ.get("TRADIER_BASE", "https://sandbox.tradier.com")
    r = httpx.post(f"{base}/v1/accounts/{acct}/orders",
                   headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                   data={"class": "multileg", "symbol": rec["symbol"],
                         "type": "market", "duration": "day"}, timeout=30)
    return f"[SANDBOX {r.status_code}] {leg}: {r.text[:160]}"


def main(argv: list[str] | None = None) -> int:
    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 40)
    ap = argparse.ArgumentParser(description="P9 multi-TF recommender")
    ap.add_argument("--as-of", default=None, help="historical date YYYY-MM-DD (causal)")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default=str(OUT_DIR / "recommendations.csv"))
    ap.add_argument("--paper", action="store_true", help="dry-run Tradier sandbox orders")
    args = ap.parse_args(argv)
    as_of = pd.Timestamp(args.as_of, tz="UTC") if args.as_of else None

    universe = load_universe("broad")
    cache = ParquetCache()
    df = scan(universe, cache, as_of)

    label = f"as-of {args.as_of}" if as_of else "current"
    print(f"=== MULTI-TF RECOMMENDATIONS ({label}) — ranked by adj. conviction ===")
    print("2h=graduated v1 (borderline); 4h/1d=Mode A. IV proxy + BS (P8 validates P&L).\n")
    if df.empty:
        print("(no live signals)")
        return 0

    cols = ["symbol", "signal_tf", "dir", "holding_bucket", "align", "conviction",
            "adj_conviction", "regime", "structure", "DTE", "long_K", "short_K",
            "net", "max_P", "max_L", "breakeven", "POP"]
    show = df[cols].head(args.top)
    print(show.to_string(index=False))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    tearsheet(df, OUT_DIR / "recommendations.png", f"Recommendations ({label})")
    print(f"\nCSV  -> {args.out}")
    print(f"chart-> {OUT_DIR / 'recommendations.png'}")

    if args.paper:
        print("\n--- PAPER (Tradier sandbox) ---")
        for r in df.head(args.top).to_dict("records"):
            print("  " + paper_submit(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
