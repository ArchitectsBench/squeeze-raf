"""Gamma Exposure (GEX) — dealer-positioning overlay for the squeeze screener.

    from reporting.gex import gex; gex("SPY")

Net GEX = Σ call(gamma·OI) − Σ put(gamma·OI), scaled by 100·spot²·0.01 ($ per 1% move),
under the standard convention (dealers long call gamma / short put gamma). Sign = regime:
  net > 0  POSITIVE gamma → dealers dampen moves → low vol, MEAN-REVERSION, PINNING
  net < 0  NEGATIVE gamma → dealers amplify moves → high vol, TRENDING/BREAKOUT
Call wall (max call gamma·OI) = resistance/target; Put wall = support — levels to "pin" a
trade. Aggregated over the nearest few expirations. Live (delayed) chain; OI is EOD so fine.
BACKTESTED 2026-07-04 (validation/gex_squeeze_backtest.py, real historical OI, 1,908
setups): the regime sign did NOT predict squeeze outcomes (NEG-gamma setups were WORSE:
47.5% vs 53.3% win @10d) and wall proximity missed its pre-registered bar. GEX no longer
adjusts decision.verdict conviction — walls/flip/profile are DISPLAY LEVELS only.
CAVEAT: dealer-sign is the standard ASSUMPTION; per-name GEX is noisier than index GEX.
"""

from __future__ import annotations

import math
import time

import pandas as pd

from reporting.tradier import TradierData

_cache: dict = {}
TTL = 900.0   # 15 min (OI is end-of-day)


def _bs_gamma(S: float, K: float, sigma: float, T: float) -> float:
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return pdf / (S * sigma * math.sqrt(T))


def _net_at(opts, S: float) -> float:
    """Net dealer GEX recomputed at hypothetical spot S (BS gamma)."""
    n = 0.0
    for K, sigma, T, oi, is_call in opts:
        c = _bs_gamma(S, K, sigma, T) * oi * 100 * S * S * 0.01
        n += c if is_call else -c
    return n


def _zero_gamma_flip(opts, spot: float) -> float | None:
    """Spot where net GEX crosses zero, via a ±20% grid + linear interpolation."""
    prev_s = prev_v = None
    for i in range(81):
        S = spot * (0.80 + 0.005 * i)
        v = _net_at(opts, S)
        if prev_v is not None and ((prev_v <= 0 <= v) or (prev_v >= 0 >= v)):
            return round(prev_s + (S - prev_s) * (0 - prev_v) / (v - prev_v), 2) if v != prev_v else round(S, 2)
        prev_s, prev_v = S, v
    return None


def gex(symbol: str, n_expiries: int = 3, refresh: bool = False) -> dict:
    now = time.time()
    hit = _cache.get(symbol)
    if hit and not refresh and now - hit[0] < TTL:
        return hit[1]
    tr = TradierData()   # prod token -> REAL-TIME chain/OI/greeks; sandbox fallback
    q = tr.quote(symbol).get("quotes", {}).get("quote", {})
    spot = float(q.get("last") or q.get("close") or 0)
    exps = tr.expirations(symbol)[:n_expiries]
    if not spot or not exps:
        data = {"symbol": symbol, "available": False}
        _cache[symbol] = (now, data)
        return data

    scale = 100 * spot * spot * 0.01
    today = pd.Timestamp.utcnow().normalize().tz_localize(None)
    net = 0.0
    call_w: dict[float, float] = {}
    put_w: dict[float, float] = {}
    strike_gex: dict[float, float] = {}
    opts = []   # (strike, iv, T, oi, is_call) for the zero-gamma flip grid
    for exp in exps:
        T = max((pd.Timestamp(exp) - today).days, 1) / 365.0
        for o in tr.chain(symbol, exp):
            gk = o.get("greeks") or {}
            g = gk.get("gamma")
            oi = o.get("open_interest") or 0
            k = o.get("strike")
            if not g or not oi or k is None:
                continue
            is_call = o.get("option_type") == "call"
            opts.append((k, gk.get("mid_iv") or 0, T, oi, is_call))
            gxoi = g * oi
            if is_call:
                call_w[k] = call_w.get(k, 0) + gxoi
                net += gxoi * scale
                strike_gex[k] = strike_gex.get(k, 0) + gxoi * scale
            else:
                put_w[k] = put_w.get(k, 0) + gxoi
                net -= gxoi * scale
                strike_gex[k] = strike_gex.get(k, 0) - gxoi * scale

    call_wall = max(call_w, key=call_w.get) if call_w else None
    put_wall = max(put_w, key=put_w.get) if put_w else None
    flip = _zero_gamma_flip(opts, spot)
    positive = net >= 0
    profile = [{"strike": k, "gex": round(v / 1e6, 1)} for k, v in sorted(strike_gex.items())]
    data = {
        "symbol": symbol, "available": True, "spot": round(spot, 2),
        "net_gex_musd": round(net / 1e6, 1),
        "regime": "positive (pinning / mean-revert)" if positive else "negative (amplify / trend)",
        "regime_sign": "POS" if positive else "NEG",
        "call_wall": call_wall, "put_wall": put_wall, "flip": flip,
        "call_wall_dist_pct": round((call_wall / spot - 1) * 100, 1) if call_wall else None,
        "put_wall_dist_pct": round((put_wall / spot - 1) * 100, 1) if put_wall else None,
        "expiries_used": exps, "profile": profile,
    }
    _cache[symbol] = (now, data)
    return data


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(gex(sys.argv[1] if len(sys.argv) > 1 else "SPY"), indent=1, default=str)[:900])
