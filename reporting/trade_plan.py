"""Trade-expression recommender — given a directional setup, how should you enter?

    from reporting.trade_plan import trade_plan; trade_plan("EW", "UP", horizon_days=15)

Transparent, rules-based vehicle selection from the LIVE option chain + our price data:
  - liquidity gate (wide bid/ask or thin OI -> stock)
  - IV regime via IV/HV ratio (cheap -> buy premium, rich -> sell premium)
  - horizon -> DTE, expected move (implied) -> strikes (by delta)
  - earnings-in-window flag (IV-crush risk)
Returns a recommended vehicle + concrete structure (strikes/DTE) + entry/target/stop +
risk/reward, plus ranked alternatives. CAVEAT: P8 showed options overlays lost on the small
squeeze move — this favors STOCK / tight debit when the edge is small; validate via backtest.
Tradier data is delayed (sandbox) — decision-support, not live execution.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from data.cache import ParquetCache
from indicators import ta
from reporting.gex import gex as compute_gex
from reporting.tradier import TradierData

ROOT = Path(__file__).resolve().parent.parent
EARN = ROOT / "data" / "earnings_dates.json"


def _pick_by_delta(chain, kind, target_abs_delta):
    cand = [o for o in chain if o.get("option_type") == kind and (o.get("greeks") or {}).get("delta") is not None]
    if not cand:
        return None
    return min(cand, key=lambda o: abs(abs((o["greeks"]["delta"])) - target_abs_delta))


def _mid(o):
    b, a = o.get("bid") or 0, o.get("ask") or 0
    return (b + a) / 2 if b and a else (a or b or 0)


def _hv(cache, symbol, days=20):
    df = cache.read(symbol, "1d")
    if df is None or len(df) < days + 5:
        return None
    r = df["close"].pct_change().dropna().tail(days)
    return float(r.std() * math.sqrt(252))


def _next_earnings_days(symbol, today):
    try:
        dates = json.loads(EARN.read_text()).get(symbol, [])
        if not dates:
            return None
        import pandas as pd
        last = pd.Timestamp(sorted(dates)[-1])
        nxt = last
        while nxt < pd.Timestamp(today):
            nxt += pd.Timedelta(days=91)            # ~quarterly estimate
        return int((nxt - pd.Timestamp(today)).days)
    except Exception:
        return None


def trade_plan(symbol: str, direction: str, horizon_days: int = 15, conviction: float = 60.0) -> dict:
    import pandas as pd
    tr = TradierData()   # prod token -> live NBBO/greeks: honest liquidity gate + real premiums
    up = direction == "UP"
    cache = ParquetCache()
    q = tr.quote(symbol).get("quotes", {}).get("quote", {})
    spot = float(q.get("last") or q.get("close") or 0)
    if not spot:
        d1 = cache.read(symbol, "1d")
        spot = float(d1["close"].iloc[-1]) if d1 is not None else 0.0
    today = str(pd.Timestamp.utcnow().date())

    # target DTE ~ 3x horizon (so theta isn't brutal), clamped
    target_dte = int(np.clip(horizon_days * 3, 21, 60))
    exps = tr.expirations(symbol)
    if not exps:
        return {"symbol": symbol, "error": "no options listed", "recommended": {"vehicle": "STOCK"}}
    exp = min(exps, key=lambda e: abs((pd.Timestamp(e) - pd.Timestamp(today)).days - target_dte))
    dte = (pd.Timestamp(exp) - pd.Timestamp(today)).days
    chain = tr.chain(symbol, exp)
    # near-month for term-structure (calendar) — ~10 DTE
    near_exp = min(exps, key=lambda e: abs((pd.Timestamp(e) - pd.Timestamp(today)).days - 10))
    near_chain = tr.chain(symbol, near_exp) if near_exp != exp else []

    # IV regime + liquidity from the ATM option
    kind = "call" if up else "put"
    atm = min([o for o in chain if o.get("option_type") == kind],
              key=lambda o: abs(o["strike"] - spot), default=None)
    iv = float((atm.get("greeks") or {}).get("mid_iv") or 0) if atm else 0.0
    hv = _hv(cache, symbol) or iv or 0.0001
    iv_hv = iv / hv if hv else 1.0
    atm_spread = ((atm.get("ask") or 0) - (atm.get("bid") or 0)) if atm else 0
    atm_mid = _mid(atm) if atm else 0
    spread_pct = (atm_spread / atm_mid) if atm_mid else 1.0
    atm_oi = (atm.get("open_interest") or 0) if atm else 0
    atr = float(ta.atr(cache.read(symbol, "1d"), 14).iloc[-1]) if cache.exists(symbol, "1d") else spot * 0.02
    exp_move = spot * iv * math.sqrt(max(dte, 1) / 365.0) if iv else atr * math.sqrt(horizon_days)
    earn_days = _next_earnings_days(symbol, today)
    earn_in_window = earn_days is not None and earn_days <= dte
    # term structure: front-month ATM IV vs back (target). >1 = inverted (front rich).
    near_atm = min([o for o in near_chain if o.get("option_type") == kind],
                   key=lambda o: abs(o["strike"] - spot), default=None) if near_chain else None
    front_iv = float((near_atm.get("greeks") or {}).get("mid_iv") or 0) if near_atm else 0.0
    term_ratio = (front_iv / iv) if (front_iv and iv) else 1.0
    term_inverted = term_ratio >= 1.10

    inputs = {"spot": round(spot, 2), "dte": dte, "exp": exp, "iv": round(iv, 3),
              "hv": round(hv, 3), "iv_hv": round(iv_hv, 2), "front_iv": round(front_iv, 3),
              "term_ratio": round(term_ratio, 2), "atm_spread_pct": round(spread_pct, 3),
              "atm_oi": atm_oi, "atr": round(atr, 2), "exp_move": round(exp_move, 2),
              "earnings_in_days": earn_days, "earnings_in_window": earn_in_window}

    gx = compute_gex(symbol)
    pos_gamma = gx.get("available") and gx.get("regime_sign") == "POS"
    illiquid = spread_pct > 0.12 or atm_oi < 50
    rich = iv_hv >= 1.25
    cheap = iv_hv <= 0.95
    low_conv = conviction < 50
    reasons = []

    # ---- vehicle rules (order matters) ----
    if illiquid:
        vehicle = "STOCK"
        reasons.append(f"options illiquid (ATM spread {spread_pct*100:.0f}% / OI {atm_oi}) → stock")
    elif low_conv and rich:
        vehicle = "IRON_CONDOR"
        reasons.append(f"low directional conviction ({conviction:.0f}) + IV rich → neutral premium harvest")
    elif term_inverted and not cheap:
        vehicle = "CALENDAR"
        reasons.append(f"term structure inverted (front/back {term_ratio:.2f}) → sell rich front, own back")
    elif rich:
        vehicle = "CREDIT_SPREAD"
        reasons.append(f"IV rich (IV/HV {iv_hv:.2f}) → sell premium")
    elif cheap and conviction >= 70:
        vehicle = "LONG_OPTION"
        reasons.append(f"IV cheap (IV/HV {iv_hv:.2f}) + high conviction → buy convexity")
    else:
        vehicle = "DEBIT_SPREAD"
        reasons.append(f"IV fair (IV/HV {iv_hv:.2f}) → debit spread (cost/theta control)")
    if earn_in_window and vehicle == "LONG_OPTION":
        vehicle = "DEBIT_SPREAD"
        reasons.append(f"earnings in ~{earn_days}d → debit spread over naked (IV-crush)")

    # GEX: neutral factual note only. The amplify/pinning regime narrative FAILED its
    # backtest (validation/gex_squeeze_backtest.py 2026-07-04 — sign was backwards);
    # walls remain useful as price LEVELS (targets/stops below), not as advice.
    if gx.get("available"):
        reasons.append(f"{'positive' if pos_gamma else 'negative'}-gamma regime — "
                       "walls shown as levels (regime not predictive per backtest)")

    structures = _build_structures(chain, kind, spot, exp_move, dte, atr, up)
    _build_advanced(structures, chain, near_chain, near_exp, kind, spot, dte)
    # "pin the trade": use gamma walls as target/stop on the STOCK structure
    cw, pw = gx.get("call_wall"), gx.get("put_wall")
    stk = structures.get("STOCK")
    if stk and gx.get("available"):
        # pin the target to a wall only when the wall is meaningfully AWAY (>=1.5%);
        # a wall at spot produced nonsense cards ("entry 434.97 -> target 435.0")
        if up and cw and cw >= spot * 1.015:
            stk["target"] = round(cw, 2)
            stk["target_note"] = "call wall (pin)"
        if (not up) and pw and pw <= spot * 0.985:
            stk["target"] = round(pw, 2)
            stk["target_note"] = "put wall (pin)"
        if up and pw and pw < spot and (spot - pw) / spot < 0.06:
            stk["stop"] = round(pw * 0.99, 2)
            stk["stop_note"] = "just below put wall"
        if (not up) and cw and cw > spot and (cw - spot) / spot < 0.06:
            stk["stop"] = round(cw * 1.01, 2)
            stk["stop_note"] = "just above call wall"

    rec = structures.get(vehicle) or structures["STOCK"]
    rec["why"] = reasons
    alts = [{"vehicle": k, **v} for k, v in structures.items() if k != rec.get("vehicle")]
    gex_block = {k: gx.get(k) for k in ("available", "regime", "regime_sign", "call_wall",
                 "put_wall", "flip", "call_wall_dist_pct", "put_wall_dist_pct", "net_gex_musd",
                 "spot", "profile")}
    return {"symbol": symbol, "direction": direction, "exp": exp, "inputs": inputs,
            "recommended": rec, "alternatives": alts, "gex": gex_block}


def _build_advanced(out, chain, near_chain, near_exp, kind, spot, dte):
    """Add CALENDAR (front/back same-strike ATM) and IRON_CONDOR (neutral) when buildable."""
    # CALENDAR: sell near ATM, buy far ATM (same kind, ~ATM strike)
    far_atm = min([o for o in chain if o.get("option_type") == kind], key=lambda o: abs(o["strike"] - spot), default=None)
    near_atm = (min([o for o in near_chain if o.get("option_type") == kind],
                    key=lambda o: abs(o["strike"] - spot), default=None) if near_chain else None)
    if far_atm and near_atm:
        debit = _mid(far_atm) - _mid(near_atm)
        if debit > 0:
            out["CALENDAR"] = {"vehicle": "CALENDAR",
                               "legs": f"sell {near_exp} {near_atm['strike']:g}{kind[0].upper()} / buy {far_atm['strike']:g}{kind[0].upper()}",
                               "net_debit": round(debit, 2), "max_loss": round(debit * 100, 0),
                               "entry": f"debit limit ~{debit:.2f}",
                               "target": "front decays → ~30-60% of debit; long vega",
                               "stop": "underlying leaves the strike / vol collapse",
                               "risk_reward": "peaks if price ≈ strike at front expiry"}
    # IRON CONDOR: sell ~0.20Δ call+put, buy ~0.10Δ wings (defined-risk neutral)
    sc, lc = _pick_by_delta(chain, "call", 0.20), _pick_by_delta(chain, "call", 0.10)
    sp, lp = _pick_by_delta(chain, "put", 0.20), _pick_by_delta(chain, "put", 0.10)
    if all([sc, lc, sp, lp]) and sc["strike"] < lc["strike"] and sp["strike"] > lp["strike"]:
        credit = (_mid(sc) - _mid(lc)) + (_mid(sp) - _mid(lp))
        width = max(lc["strike"] - sc["strike"], sp["strike"] - lp["strike"])
        if credit > 0:
            out["IRON_CONDOR"] = {"vehicle": "IRON_CONDOR",
                                  "legs": f"sell {sp['strike']:g}P/{sc['strike']:g}C, buy {lp['strike']:g}P/{lc['strike']:g}C",
                                  "net_credit": round(credit, 2), "width": width,
                                  "max_profit": round(credit * 100, 0), "max_loss": round((width - credit) * 100, 0),
                                  "risk_reward": round(credit / (width - credit), 2) if (width - credit) > 0 else None,
                                  "p_profit_est": round(1 - 2 * 0.20, 2),
                                  "entry": f"credit limit ~{credit:.2f}", "target": "~50% of credit",
                                  "stop": "a short strike breached",
                                  "payoff_legs": [
                                      {"type": "put", "side": -1, "strike": sp["strike"], "premium": round(_mid(sp), 2)},
                                      {"type": "put", "side": 1, "strike": lp["strike"], "premium": round(_mid(lp), 2)},
                                      {"type": "call", "side": -1, "strike": sc["strike"], "premium": round(_mid(sc), 2)},
                                      {"type": "call", "side": 1, "strike": lc["strike"], "premium": round(_mid(lc), 2)}]}


def _build_structures(chain, kind, spot, exp_move, dte, atr, up):
    out = {}
    sgn = 1 if up else -1
    # STOCK
    out["STOCK"] = {"vehicle": "STOCK",
                    "entry": round(spot, 2), "target": round(spot + sgn * exp_move, 2),
                    "stop": round(spot - sgn * 1.5 * atr, 2),
                    "risk_reward": round(exp_move / (1.5 * atr), 2) if atr else None,
                    "max_loss": "use stop (≈%.2f/sh)" % (1.5 * atr),
                    # payoff_legs: machine-readable for the UI payoff diagram
                    "payoff_legs": [{"type": "stock", "side": sgn, "basis": round(spot, 2)}]}
    # LONG_OPTION (~0.55 delta)
    lo = _pick_by_delta(chain, kind, 0.55)
    if lo:
        cost = _mid(lo)
        out["LONG_OPTION"] = {"vehicle": "LONG_OPTION", "legs": f"buy {lo['symbol']}",
                              "strike": lo["strike"], "cost_per_contract": round(cost * 100, 0),
                              "entry": f"limit ~{cost:.2f}", "max_loss": round(cost * 100, 0),
                              "target": f"~{(cost*2):.2f} (≈2x) or exp-move", "stop": "≈50% of premium",
                              "risk_reward": "open-ended (convex)",
                              "payoff_legs": [{"type": kind, "side": 1, "strike": lo["strike"],
                                               "premium": round(cost, 2)}]}
    # DEBIT_SPREAD (buy ~0.60 / sell ~0.30)
    lng, sht = _pick_by_delta(chain, kind, 0.60), _pick_by_delta(chain, kind, 0.30)
    if lng and sht and lng["strike"] != sht["strike"]:
        debit = _mid(lng) - _mid(sht)
        width = abs(sht["strike"] - lng["strike"])
        if debit > 0:
            out["DEBIT_SPREAD"] = {"vehicle": "DEBIT_SPREAD",
                                   "legs": f"buy {lng['strike']:g} / sell {sht['strike']:g} {kind}",
                                   "net_debit": round(debit, 2), "width": width,
                                   "max_loss": round(debit * 100, 0), "max_profit": round((width - debit) * 100, 0),
                                   "risk_reward": round((width - debit) / debit, 2) if debit else None,
                                   "breakeven": round(lng["strike"] + sgn * debit, 2),
                                   "entry": f"debit limit ~{debit:.2f}", "target": "~70-80% of max", "stop": "thesis break / 1.5x debit",
                                   "payoff_legs": [
                                       {"type": kind, "side": 1, "strike": lng["strike"], "premium": round(_mid(lng), 2)},
                                       {"type": kind, "side": -1, "strike": sht["strike"], "premium": round(_mid(sht), 2)}]}
    # CREDIT_SPREAD (sell ~0.30 / buy ~0.15) — opposite side: bull-put for UP, bear-call for DOWN
    ckind = "put" if up else "call"
    csht, clng = _pick_by_delta(chain, ckind, 0.30), _pick_by_delta(chain, ckind, 0.15)
    if csht and clng and csht["strike"] != clng["strike"]:
        credit = _mid(csht) - _mid(clng)
        width = abs(csht["strike"] - clng["strike"])
        if credit > 0:
            out["CREDIT_SPREAD"] = {"vehicle": "CREDIT_SPREAD",
                                    "legs": f"sell {csht['strike']:g} / buy {clng['strike']:g} {ckind} ({'bull-put' if up else 'bear-call'})",
                                    "net_credit": round(credit, 2), "width": width,
                                    "max_profit": round(credit * 100, 0), "max_loss": round((width - credit) * 100, 0),
                                    "risk_reward": round(credit / (width - credit), 2) if (width - credit) > 0 else None,
                                    "p_profit_est": round(1 - abs((csht.get("greeks") or {}).get("delta") or 0.3), 2),
                                    "entry": f"credit limit ~{credit:.2f}", "target": "~50% of credit", "stop": "≈2x credit / short strike breach",
                                    "payoff_legs": [
                                        {"type": ckind, "side": -1, "strike": csht["strike"], "premium": round(_mid(csht), 2)},
                                        {"type": ckind, "side": 1, "strike": clng["strike"], "premium": round(_mid(clng), 2)}]}
    return out
