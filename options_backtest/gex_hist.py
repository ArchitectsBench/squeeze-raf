"""Historical GEX from cached ORATS days — DELIBERATELY the same math as the live
engine (reporting/gex.py) so the backtest validates the LIVE signal, not a proxy.

    gex_at(adapter, "ROP", "2026-05-01")

Per strike row ORATS gives ONE gamma (BS gamma is call/put-identical at a strike/
expiry/vol), applied against both cOi and pOi. Net GEX = (Σ call gamma·OI − Σ put
gamma·OI)·100·spot²·0.01; regime sign; call/put walls = argmax(gamma·OI); flip via the
same ±20% BS-gamma grid zero-cross (using cMidIv + yte). OI is end-of-day, as live.
"""

from __future__ import annotations

import pandas as pd

from reporting.gex import _zero_gamma_flip


def gex_at(adapter, ticker: str, date, n_expiries: int = 3) -> dict:
    """Dealer-gamma snapshot for one ticker on one historical day (EOD)."""
    ch = adapter.chain(ticker, date)
    if ch is None or "cOi" not in ch.columns:
        return {"symbol": ticker, "available": False}
    ch = ch[(ch["gamma"] > 0) & ((ch["cOi"] > 0) | (ch["pOi"] > 0))]
    if not len(ch):
        return {"symbol": ticker, "available": False}
    spot = float(ch["stkPx"].iloc[0])
    exps = sorted(ch["expiry"].unique())[:n_expiries]
    ch = ch[ch["expiry"].isin(exps)]
    if not spot or not len(ch):
        return {"symbol": ticker, "available": False}

    scale = 100 * spot * spot * 0.01
    net = 0.0
    call_w: dict[float, float] = {}
    put_w: dict[float, float] = {}
    opts = []   # (strike, iv, T, oi, is_call) for the flip grid
    for r in ch.itertuples():
        g, k, T = float(r.gamma), float(r.strike), max(float(r.yte), 1 / 365.0)
        iv = float(r.cMidIv or 0)
        coi, poi = int(r.cOi or 0), int(r.pOi or 0)
        if coi:
            call_w[k] = call_w.get(k, 0) + g * coi
            net += g * coi * scale
            opts.append((k, iv, T, coi, True))
        if poi:
            put_w[k] = put_w.get(k, 0) + g * poi
            net -= g * poi * scale
            opts.append((k, iv, T, poi, False))

    call_wall = max(call_w, key=call_w.get) if call_w else None
    put_wall = max(put_w, key=put_w.get) if put_w else None
    return {
        "symbol": ticker, "available": True, "spot": round(spot, 2),
        "net_gex_musd": round(net / 1e6, 1),
        "regime_sign": "POS" if net >= 0 else "NEG",
        "call_wall": call_wall, "put_wall": put_wall,
        "flip": _zero_gamma_flip(opts, spot),
        "call_wall_dist_pct": round((call_wall / spot - 1) * 100, 1) if call_wall else None,
        "put_wall_dist_pct": round((put_wall / spot - 1) * 100, 1) if put_wall else None,
        "expiries_used": [str(pd.Timestamp(e).date()) for e in exps],
    }
