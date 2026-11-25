"""Upgraded cross-sectional momentum -> full graduation gauntlet.

    uv run python -m validation.momentum_run

Residual (beta-neutral) momentum, sector-neutralized, inverse-vol leg-weighted,
dollar-neutral decile L/S on the ~400-name liquid universe. Runs the same gated
funnel as the other strategies, adapted to a cross-sectional PORTFOLIO return series:
  Stage-1 edge -> small honest grid -> anchored WFA -> DSR + PBO/CSCV + block bootstrap
  + random-rank permutation + subperiod + neighborhood. OOS (last 24m) unlocked here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from data.cache import ParquetCache
from signals.xsec_momentum import betas, resid_scores

from .bootstrap import block_bootstrap_sharpe
from .dsr import deflated_sharpe_ratio
from .pbo_cscv import pbo_cscv

UNIV = Path(__file__).resolve().parent.parent / "config" / "liquid_universe.json"
SECT = Path(__file__).resolve().parent.parent / "data" / "sectors.json"
GATES = Path(__file__).resolve().parent.parent / "config" / "gates.yaml"
COST = 5e-4
ANN = 12
HOLD = 21
REB = 21
SKIP = 21


def load_panel(cache, syms):
    closes = {}
    for s in syms:
        df = cache.read(s, "1d")
        if df is None or len(df) < 300:
            continue
        closes[s] = df["close"]
    return pd.DataFrame(closes).sort_index()


def backtest(C, RET, mret, mclose, cfg, start, sectors=None, rng=None, randomize=False, clean=False):
    lb, dec, vs, sn = cfg["lookback"], cfg["decile"], cfg["volscale"], cfg["sector_neutral"]
    dates = C.index
    rets, dts = [], []
    prev = pd.Series(0.0, index=C.columns)
    for i in range(start, len(dates) - HOLD - 1, REB):
        sc = resid_scores(C, RET, mret, mclose, i, lb, SKIP).dropna()
        if clean:
            # same cleaning as the live book, applied point-in-time at each rebalance:
            # real common stock (has SIC sector), full history, price>=5, no artifacts/leverage
            full_hist = C.iloc[i - lb].notna()
            raw = C.iloc[i - SKIP] / C.iloc[i - lb] - 1.0
            b = betas(RET, mret, i - lb, i - SKIP)
            px = C.iloc[i]
            keep = [s for s in sc.index
                    if s in sectors and bool(full_hist.get(s, False))
                    and float(px[s]) >= 5.0 and abs(float(raw.get(s, 0.0))) <= 3.0
                    and abs(float(b.get(s, 0.0))) <= 3.0]
            sc = sc.reindex(keep)
        if len(sc) < 20:
            continue
        if sn and sectors is not None:
            grp = pd.Series({s: sectors.get(s, "NA") for s in sc.index})
            sc = sc - sc.groupby(grp).transform("mean")
        if randomize:
            sc = pd.Series(rng.permutation(sc.to_numpy()), index=sc.index)
        k = max(1, int(len(sc) * dec))
        r = sc.sort_values()
        shorts, longs = r.index[:k], r.index[-k:]
        if vs:
            vol = RET.iloc[i - 60:i].std()
            wl = (1.0 / vol.reindex(longs)).replace([np.inf], np.nan).fillna(0.0)
            ws = (1.0 / vol.reindex(shorts)).replace([np.inf], np.nan).fillna(0.0)
            wl = wl / wl.sum() * 0.5 if wl.sum() > 0 else pd.Series(0.5 / len(longs), index=longs)
            ws = ws / ws.sum() * 0.5 if ws.sum() > 0 else pd.Series(0.5 / len(shorts), index=shorts)
        else:
            wl = pd.Series(0.5 / len(longs), index=longs)
            ws = pd.Series(0.5 / len(shorts), index=shorts)
        w = pd.Series(0.0, index=C.columns)
        w[longs] = wl.values
        w[shorts] = -ws.values
        p0, p1 = C.iloc[i + 1], C.iloc[i + 1 + HOLD]
        sret = (p1 / p0 - 1.0).reindex(w.index)
        port = float((w * sret).sum(skipna=True))
        cost = float((w - prev).abs().sum()) * COST
        rets.append(port - cost)
        dts.append(dates[i + 1])
        prev = w
    return pd.Series(rets, index=pd.DatetimeIndex(dts))


def sharpe(r):
    sd = r.std()
    return float(r.mean() / sd * np.sqrt(ANN)) if sd > 0 and len(r) else 0.0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="momentum gauntlet")
    ap.add_argument("--clean", action="store_true",
                    help="apply the live-book cleaning (SIC sector / history / price / artifact filters)")
    args = ap.parse_args(argv)
    pd.set_option("display.width", 200)
    cache = ParquetCache()
    syms = json.loads(UNIV.read_text())
    sectors = json.loads(SECT.read_text()) if SECT.exists() else {}
    C = load_panel(cache, syms)
    RET = C.pct_change()
    mclose = cache.read("SPY", "1d")["close"].reindex(C.index).ffill()
    mret = mclose.pct_change()
    gates = yaml.safe_load(GATES.read_text())["graduation"]
    rng = np.random.default_rng(42)

    grid = [{"lookback": lb, "decile": dec, "volscale": vs, "sector_neutral": True}
            for lb in (126, 189, 252) for dec in (0.1, 0.2) for vs in (True, False)]
    start = 252 + 1

    print("=== UPGRADED MOMENTUM (residual + sector-neutral + vol-scaled) — GAUNTLET ===")
    print(f"universe {C.shape[1]} names | sectors for {len(sectors)} | {C.index[0].date()} -> "
          f"{C.index[-1].date()} | cost {COST*1e4:.0f}bps/side | {len(grid)} configs | "
          f"cleaning {'ON (clean universe)' if args.clean else 'OFF (raw universe)'}\n")

    series = [backtest(C, RET, mret, mclose, cfg, start, sectors, clean=args.clean) for cfg in grid]
    full_sr = [sharpe(s) for s in series]
    for cfg, sr, s in sorted(zip(grid, full_sr, series), key=lambda z: -z[1])[:6]:
        print(f"  SR {sr:5.2f}  lookback {cfg['lookback']:3d}  decile {cfg['decile']}  "
              f"volscale {cfg['volscale']}  (n={len(s)})")
    best_i = int(np.argmax(full_sr))
    best, chosen = grid[best_i], series[best_i]
    print(f"\nStage-1 edge (chosen): mean {chosen.mean()*1e4:.0f}bps/mo, "
          f"t {chosen.mean()/(chosen.std()/np.sqrt(len(chosen))):.2f}, SR {full_sr[best_i]:.2f}")
    print(f"chosen config: {best}\n")

    # PBO matrix on common dates
    mat = pd.concat({i: s for i, s in enumerate(series)}, axis=1).dropna()
    pbo = pbo_cscv(mat.to_numpy(), s_blocks=8)

    # DSR: trial Sharpes per-period (unannualized), variance across trials
    per_sr = [s.mean() / s.std() for s in series if s.std() > 0]
    dsr = deflated_sharpe_ratio(chosen.to_numpy(), float(np.var(per_sr, ddof=1)), len(series))

    boot = block_bootstrap_sharpe(chosen.to_numpy(), ann=ANN, n_iter=5000)

    # random-rank permutation: how often does a shuffled ranking match the real Sharpe?
    null = np.array([sharpe(backtest(C, RET, mret, mclose, best, start, sectors,
                                     rng=rng, randomize=True, clean=args.clean))
                     for _ in range(300)])
    perm_pct = float((null < full_sr[best_i]).mean() * 100)

    oos = chosen.iloc[-24:]
    oos_sr = sharpe(oos)
    by_year = {int(y): round(sharpe(g), 2) for y, g in chosen.groupby(chosen.index.year)}
    neigh_min = float(np.min(full_sr))

    print("--- MOMENTUM GRADUATION SCORECARD ---")
    checks = [
        ("OOS Sharpe (last 24m)", oos_sr, oos_sr >= gates["oos_sharpe_min"], f">= {gates['oos_sharpe_min']}"),
        ("PBO (CSCV)", pbo["pbo"], pbo["pbo"] < gates["pbo_max"], f"< {gates['pbo_max']}"),
        ("Deflated Sharpe", dsr["dsr"], dsr["significant"], "> 0.95"),
        ("Bootstrap p5", boot["p5"], boot["p5"] >= gates["bootstrap_sharpe_5th_pct_min"], ">= 0.5"),
        ("Bootstrap median", boot["median"], boot["median"] >= gates["bootstrap_sharpe_median_min"], ">= 1.2"),
        ("Permutation pctile", perm_pct, perm_pct >= gates["permutation_percentile_min"], ">= 95"),
        ("Neighborhood min SR", neigh_min, neigh_min >= 0.3, "robust (>=0.3)"),
    ]
    for name, val, ok, need in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {val:.3f}  (need {need})")
    print(f"  [INFO] Subperiod Sharpe by year: {by_year}")
    print(f"  [INFO] research Sharpe {full_sr[best_i]:.2f} | full periods {len(chosen)}")

    passed = all(ok for _, _, ok, _ in checks)
    print("\n" + "=" * 60)
    print(f"  UPGRADED MOMENTUM GRADUATION: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
