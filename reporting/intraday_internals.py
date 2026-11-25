"""Intraday market-internals PROXY from our 5-minute universe (no exchange feed needed).

True NYSE $TICK/$TRIN aren't in our free data, so we approximate them across the ~400-name
common-stock universe at 5-minute granularity for the latest session:
  - TICK-proxy: per-bar (#names up − #names down)
  - $ADD-proxy: cumulative advancers − decliners on the day
  - TRIN-proxy: (adv/dec) / (upVol/downVol)   (>1 weak, <1 strong)
  - pct_up: names up vs their session open
Directionally useful (coarser than the ~3000-stock NYSE feed) — flagged as a proxy.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from data.cache import ParquetCache

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "config" / "common_stocks.json"
_cache: dict = {"ts": 0.0, "data": None}
TTL = 300.0


def intraday_internals(refresh: bool = False) -> dict:
    now = time.time()
    if not refresh and _cache["data"] and now - _cache["ts"] < TTL:
        return _cache["data"]
    cache = ParquetCache()
    syms = json.loads(CS.read_text())
    closes, opens, vols = {}, {}, {}
    for s in syms:
        df = cache.read(s, "5min")
        if df is None or len(df) < 10:
            continue
        last_day = df.index[-1].date()
        d = df[df.index.map(lambda t: t.date() == last_day)]
        if len(d) < 2:
            continue
        closes[s] = d["close"]
        opens[s] = float(d["open"].iloc[0])
        vols[s] = d["volume"]
    if not closes:
        data = {"available": False}
        _cache.update(ts=now, data=data)
        return data
    C = pd.DataFrame(closes).sort_index()
    asof = C.index[-1]
    # per-bar tick proxy: names up vs down on each 5m bar
    chg = C.diff()
    tick = ((chg > 0).sum(axis=1) - (chg < 0).sum(axis=1))
    tick_series = [{"t": str(ts)[11:16], "v": int(val)} for ts, val in tick.iloc[1:].items()]
    # day stats: latest close vs session open
    last = C.iloc[-1]
    op = pd.Series(opens)
    up = (last > op)
    adv, dec = int(up.sum()), int((last < op).sum())
    pct_up = float(up.mean())
    V = pd.DataFrame(vols).sort_index()
    daychg = C.iloc[-1] - C.iloc[0]
    upvol = float(V.sum()[daychg > 0].sum())
    downvol = float(V.sum()[daychg < 0].sum())
    trin = ((adv / max(dec, 1)) / (upvol / max(downvol, 1))) if upvol and downvol else float("nan")
    add_proxy = int(tick.cumsum().iloc[-1])
    label = ("risk-on" if pct_up > 0.55 and add_proxy > 0
             else "risk-off" if pct_up < 0.45 and add_proxy < 0 else "mixed")
    # 0-100 intraday breadth score
    score = float(np.clip(pct_up * 100, 0, 100))
    data = {"available": True, "asof": str(asof.date()), "session_bars": len(C),
            "pct_up": round(pct_up, 3), "adv": adv, "dec": dec,
            "add_proxy": add_proxy, "trin_proxy": round(trin, 2) if trin == trin else None,
            "score": round(score, 1), "label": label, "tick_series": tick_series[-78:]}
    _cache.update(ts=now, data=data)
    return data
