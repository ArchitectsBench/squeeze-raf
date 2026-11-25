"""FastAPI backend for the squeeze screener web app.

    uv run uvicorn webapp.api.main:app --reload --port 8000

Serves the multi-timeframe confluence matrix + per-cell charts, all computed from the
parquet cache via the shared reporting.screener_core (same logic as the CLI / ntfy).
Read-only over the cache; data freshness is handled separately by the refresher/cron.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import asyncio
import os
import threading

import httpx
import pandas as pd
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data.cache import ParquetCache
from indicators.squeeze import momentum_hist, squeeze_state
from indicators.ta import sma
from reporting.decision import verdict
from reporting.gex import gex as compute_gex
from reporting.intraday_internals import intraday_internals
from reporting.screener_core import classify
from reporting.tape_score import tape_history, tape_score
from reporting.trade_plan import trade_plan as build_trade_plan

try:
    from data.base import load_dotenv
    load_dotenv()
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
UNIV = json.loads((ROOT / "config" / "liquid_universe.json").read_text())
_cs_path = ROOT / "config" / "common_stocks.json"
CS = set(json.loads(_cs_path.read_text())) if _cs_path.exists() else None
SYMBOLS = [s for s in UNIV if CS is None or s in CS]

TIMEFRAMES = ["5min", "15min", "30min", "1h", "2h", "3h", "4h", "1d", "2d", "3d", "4d", "1w"]
LENGTH = 20
TTL = 60.0   # seconds: re-scan the cache at most this often (data refresh is separate)

app = FastAPI(title="Squeeze Screener")
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"], allow_headers=["*"],
)
_mx: dict = {"ts": 0.0, "data": None}
_mx_lock = threading.Lock()


def build_matrix() -> dict:
    cache = ParquetCache()
    asof: dict[str, str] = {}
    rows = []
    for s in SYMBOLS:
        cells = {}
        for tf in TIMEFRAMES:
            df = cache.read(s, tf)
            if df is None or len(df) == 0:
                continue
            last = str(df.index[-1].date())
            if tf not in asof or last > asof[tf]:
                asof[tf] = last
            c = classify(df, length=LENGTH)
            if c and c["tier"]:
                cells[tf] = c
        if not cells:
            continue
        up = sum(1.0 if v["tier"] == "ALERT" else 0.5 for v in cells.values() if v["dir"] == "UP")
        dn = sum(1.0 if v["tier"] == "ALERT" else 0.5 for v in cells.values() if v["dir"] == "DOWN")
        conf_dir = "UP" if up >= dn else "DOWN"
        rows.append({
            "symbol": s, "cells": cells,
            "conf_score": round(max(up, dn), 1), "conf_dir": conf_dir,
            "conf_n": sum(1 for v in cells.values() if v["dir"] == conf_dir),
        })
    rows.sort(key=lambda r: (-r["conf_score"], -r["conf_n"]))
    return {"timeframes": TIMEFRAMES, "asof": asof, "tickers": rows,
            "computed_at": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/api/matrix")
def matrix(refresh: bool = False) -> dict:
    """Served from the precomputed cache — the background warmer keeps it fresh, so
    requests never pay the ~10s build. Only a forced refresh or cold boot builds."""
    if refresh or _mx["data"] is None:
        with _mx_lock:                 # serialize builds — no thundering herd
            if refresh or _mx["data"] is None:
                _mx["data"] = build_matrix()
                _mx["ts"] = time.time()
    return _mx["data"]


_INTRADAY_TFS = {"5min", "15min", "30min", "1h", "2h", "3h", "4h"}


def _rvol(df, tf: str):
    """Relative volume per bar. Intraday: volume vs the mean of the SAME
    time-of-day bar over the prior 10 sessions (intraday volume is U-shaped, so a
    plain rolling mean misleads). Daily/multiday: volume vs its prior 20-bar mean.
    Current bar excluded from its own baseline (shift 1)."""
    vol = df["volume"].astype(float)
    if tf in _INTRADAY_TFS:
        tod = df.index.tz_convert("America/New_York").strftime("%H:%M")
        base = vol.groupby(tod).transform(
            lambda s: s.shift(1).rolling(10, min_periods=3).mean())
    else:
        base = vol.shift(1).rolling(20, min_periods=5).mean()
    return (vol / base).replace([float("inf")], None)


@app.get("/api/chart")
def chart(symbol: str, tf: str = "1d", bars: int = 120) -> dict:
    cache = ParquetCache()
    df = cache.read(symbol, tf)
    if df is None or len(df) == 0:
        return {"symbol": symbol, "tf": tf, "bars": []}
    st = squeeze_state(df, length=LENGTH)
    mom = momentum_hist(df, length=LENGTH)
    ma = sma(df["close"], LENGTH)
    rvol = _rvol(df, tf)
    sub = df.tail(bars)
    out = []
    for ts, r in sub.iterrows():
        rv = rvol.loc[ts]
        out.append({
            "t": str(ts), "o": round(float(r["open"]), 2), "h": round(float(r["high"]), 2),
            "l": round(float(r["low"]), 2), "c": round(float(r["close"]), 2),
            "v": int(r["volume"]), "state": int(st.loc[ts]),
            "mom": round(float(mom.loc[ts]), 3),
            "ma": None if ma.loc[ts] != ma.loc[ts] else round(float(ma.loc[ts]), 2),
            "rvol": None if rv != rv or rv is None else round(float(rv), 2),
        })
    return {"symbol": symbol, "tf": tf, "bars": out}


NEWS_TTL = 1800.0   # 30 min — news doesn't change minute-to-minute
NEWS_WINDOW_DAYS = 7  # sentiment badge counts only articles this fresh (validated ~6d window)
_news: dict = {}


async def fetch_news_async(client: httpx.AsyncClient, symbol: str) -> dict:
    """Async + cached so it runs on the event loop (NOT the threadpool) and never starves
    the synchronous matrix endpoint."""
    now = time.time()
    hit = _news.get(symbol)
    if hit and now - hit[0] < NEWS_TTL:
        return hit[1]
    key = os.environ.get("POLYGON_API_KEY", "")
    items, breakdown = [], {"positive": 0, "negative": 0, "neutral": 0}
    # The VALIDATED news filter (catalyst backtest) used a ~6-day sentiment window.
    # Without a date floor the last-10 articles can be months old (measured: ROP 46-162d)
    # and the badge scores stale news. Headlines list keeps everything for display;
    # the SENTIMENT badge counts only articles fresher than NEWS_WINDOW_DAYS.
    fresh_floor = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=NEWS_WINDOW_DAYS)).isoformat()
    try:
        r = await client.get("https://api.polygon.io/v2/reference/news",
                             params={"ticker": symbol, "limit": 10, "apiKey": key})
        for a in r.json().get("results", []):
            ins = next((i for i in (a.get("insights") or []) if i.get("ticker") == symbol), None)
            s = (ins or {}).get("sentiment")
            fresh = (a.get("published_utc") or "") >= fresh_floor
            if s in breakdown and fresh:
                breakdown[s] += 1
            items.append({
                "title": a.get("title"), "publisher": (a.get("publisher") or {}).get("name"),
                "published": a.get("published_utc"), "url": a.get("article_url"),
                "sentiment": s, "reasoning": (ins or {}).get("sentiment_reasoning"),
                "fresh": fresh,
            })
    except Exception:
        pass
    net = ("positive" if breakdown["positive"] > breakdown["negative"]
           else "negative" if breakdown["negative"] > breakdown["positive"] else "neutral")
    data = {"symbol": symbol, "count": len(items), "sentiment": net,
            "breakdown": breakdown, "items": items}
    _news[symbol] = (now, data)
    return data


@app.get("/api/news")
async def news(symbol: str) -> dict:
    """Full recent headlines + per-ticker sentiment for one symbol (cached 30 min)."""
    async with httpx.AsyncClient(timeout=15) as client:
        return await fetch_news_async(client, symbol)


@app.get("/api/news_summary")
async def news_summary(symbols: str) -> dict:
    """Compact {symbol: {count, sentiment}} for badges — fetched CONCURRENTLY (capped)."""
    syms = [s.strip() for s in symbols.split(",")[:80] if s.strip()]
    sem = asyncio.Semaphore(8)

    async def one(client, s):
        async with sem:
            d = await fetch_news_async(client, s)
        return s, {"count": d["count"], "sentiment": d["sentiment"]}

    async with httpx.AsyncClient(timeout=15) as client:
        results = await asyncio.gather(*[one(client, s) for s in syms])
    return dict(results)


@app.get("/api/tape")
def tape(refresh: bool = False) -> dict:
    """Market-internals Tape Score (0-100 risk-on/off) — display-only context (cached 5m)."""
    return tape_score(refresh=refresh)


@app.get("/api/tape/history")
def tape_hist(days: int = 120) -> dict:
    """Daily tape + component history for sparklines."""
    return {"history": tape_history(days=days)}


@app.get("/api/internals/intraday")
def intraday() -> dict:
    """Intraday TICK/TRIN/breadth PROXY from our 5m universe (cached 5m)."""
    return intraday_internals()


def _best_tier(t: dict) -> str | None:
    tiers = [c.get("tier") for c in t["cells"].values()]
    return "ALERT" if "ALERT" in tiers else ("WATCH" if "WATCH" in tiers else None)


_dec = {"ts": 0.0, "data": None}
DEC_TTL = 120.0


async def _get_matrix() -> dict:
    if _mx["data"] is None:
        _mx["data"] = await asyncio.to_thread(build_matrix)
        _mx["ts"] = time.time()
    return _mx["data"]


async def _build_decisions() -> dict:
    mx = await _get_matrix()
    tp = await asyncio.to_thread(tape_score)
    intra = await asyncio.to_thread(intraday_internals)
    tape_s, intra_s = tp["score"], intra.get("score")
    syms = [t["symbol"] for t in mx["tickers"]]
    sem = asyncio.Semaphore(8)

    async def one(client, s):
        async with sem:
            d = await fetch_news_async(client, s)
        return s, d["sentiment"]

    async with httpx.AsyncClient(timeout=15) as client:
        sent = dict(await asyncio.gather(*[one(client, s) for s in syms]))
    out = {t["symbol"]: verdict(t["conf_dir"], _best_tier(t), t["conf_n"], t["conf_score"],
                                tape_s, sent.get(t["symbol"]), intra_s) for t in mx["tickers"]}
    return {"tape": tape_s, "tape_label": tp["label"], "intraday": intra_s, "verdicts": out}


@app.get("/api/decisions")
async def decisions() -> dict:
    """Per-setup GO/CAUTION/AVOID for every screener ticker — precomputed by the warmer."""
    if _dec["data"] is None:
        _dec["data"] = await _build_decisions()
        _dec["ts"] = time.time()
    return _dec["data"]


@app.get("/api/gex")
async def gex_ep(symbol: str) -> dict:
    """Gamma-exposure overlay for one ticker (regime, call/put walls, flip)."""
    return await asyncio.to_thread(compute_gex, symbol)


_deep: dict[str, dict] = {}   # symbol -> {"ts", "decision", "plan"} — warmed round-robin
DEEP_TTL = 900.0              # matches the GEX cache; OI/chains are slow-moving


async def _build_decision(symbol: str) -> dict:
    mx = await _get_matrix()
    tp = await asyncio.to_thread(tape_score)
    intra = await asyncio.to_thread(intraday_internals)
    gx = await asyncio.to_thread(compute_gex, symbol)
    t = next((x for x in mx["tickers"] if x["symbol"] == symbol), None)
    async with httpx.AsyncClient(timeout=15) as client:
        news_data = await fetch_news_async(client, symbol)
    if t is None:
        return {"symbol": symbol, "verdict": None, "note": "no current screener setup",
                "tape": tp, "intraday": intra, "news": news_data, "gex": gx}
    v = verdict(t["conf_dir"], _best_tier(t), t["conf_n"], t["conf_score"],
                tp["score"], news_data["sentiment"], intra.get("score"), gex=gx)
    return {"symbol": symbol, "verdict": v, "setup": {"dir": t["conf_dir"], "tier": _best_tier(t),
            "conf_n": t["conf_n"], "conf_score": t["conf_score"], "cells": t["cells"]},
            "tape": tp, "intraday": intra, "news": news_data, "gex": gx}


async def _build_plan(symbol: str, direction: str | None = None, horizon: int = 15) -> dict:
    mx = await _get_matrix()
    t = next((x for x in mx["tickers"] if x["symbol"] == symbol), None)
    d = direction or (t["conf_dir"] if t else None)
    if not d:
        return {"symbol": symbol, "error": "no current setup — pass direction=UP or DOWN"}
    conviction = 60.0
    if t:
        tp = await asyncio.to_thread(tape_score)
        intra = await asyncio.to_thread(intraday_internals)
        async with httpx.AsyncClient(timeout=15) as client:
            nd = await fetch_news_async(client, symbol)
        conviction = verdict(t["conf_dir"], _best_tier(t), t["conf_n"], t["conf_score"],
                             tp["score"], nd["sentiment"], intra.get("score"))["conviction"]
    return await asyncio.to_thread(build_trade_plan, symbol, d, int(horizon), float(conviction))


async def _warm_deep(symbol: str) -> dict:
    entry = {"decision": await _build_decision(symbol),
             "plan": await _build_plan(symbol), "ts": time.time()}
    _deep[symbol] = entry
    return entry


@app.get("/api/decision")
async def decision(symbol: str) -> dict:
    """Full decisive read for one ticker — precomputed round-robin by the warmer;
    computed on demand only for symbols the warmer hasn't reached yet."""
    hit = _deep.get(symbol)
    if hit and time.time() - hit["ts"] < DEEP_TTL:
        return hit["decision"]
    return (await _warm_deep(symbol))["decision"]


@app.get("/api/trade_plan")
async def trade_plan_ep(symbol: str, direction: str | None = None, horizon: int = 15) -> dict:
    """How to ENTER — default form served from the precomputed deep cache; custom
    direction/horizon computed live."""
    if direction is None and horizon == 15:
        hit = _deep.get(symbol)
        if hit and time.time() - hit["ts"] < DEEP_TTL:
            return hit["plan"]
        return (await _warm_deep(symbol))["plan"]
    return await _build_plan(symbol, direction, horizon)


_trades = {"ts": 0.0, "data": None}
_trades_lock = asyncio.Lock()
TRADES_TTL = 60.0


def _build_trades() -> dict:
    from reporting.track import position_rows
    from reporting.tradier import Tradier
    tr = Tradier()
    rows = position_rows(tr)
    realized = sum(r.get("pnl$", 0) or 0 for r in rows if r.get("realized"))
    unreal = sum(r.get("pnl$", 0) or 0 for r in rows
                 if r.get("pnl$") is not None and not r.get("realized"))
    return {"account": tr.account, "sandbox": tr.is_sandbox, "rows": rows,
            "realized": round(realized, 2), "unrealized": round(unreal, 2),
            "total": round(realized + unreal, 2),
            "computed_at": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/api/trades")
async def trades(refresh: bool = False) -> dict:
    """Open + closed paper positions with P&L and brackets — precomputed by the warmer.
    Reads only OUR order ids — shared-account safe, same path as reporting.track."""
    if not refresh and _trades["data"] is not None:
        return _trades["data"]
    async with _trades_lock:
        if refresh or _trades["data"] is None:
            _trades["data"] = await asyncio.to_thread(_build_trades)
            _trades["ts"] = time.time()
    return _trades["data"]


# ---------------- background warmer: proactive, not reactive ----------------
WARM_TICK = 20.0
_warm_state = {"rr": 0, "last": None, "errors": 0}


async def _warmer() -> None:
    """Keeps every cache hot so requests never compute: matrix (~10s build), tape,
    intraday, trades, batch decisions+news, and a round-robin DEEP warm (decision +
    trade plan + GEX) over the matrix tickers. Pacing: one deep symbol per tick =
    ~3/min x ~5 Tradier calls ~= 15 req/min, well under the sandbox 60/min."""
    await asyncio.sleep(1)
    while True:
        t0 = time.time()
        try:
            if _mx["data"] is None or time.time() - _mx["ts"] > TTL * 0.75:
                _mx["data"] = await asyncio.to_thread(build_matrix)
                _mx["ts"] = time.time()
            await asyncio.to_thread(tape_score)           # each keeps its own TTL
            await asyncio.to_thread(intraday_internals)
            if _dec["data"] is None or time.time() - _dec["ts"] > DEC_TTL * 0.75:
                _dec["data"] = await _build_decisions()
                _dec["ts"] = time.time()
            if _trades["data"] is None or time.time() - _trades["ts"] > TRADES_TTL * 0.75:
                _trades["data"] = await asyncio.to_thread(_build_trades)
                _trades["ts"] = time.time()
            syms = [t["symbol"] for t in (_mx["data"] or {}).get("tickers", [])]
            if syms:
                s = syms[_warm_state["rr"] % len(syms)]
                _warm_state["rr"] += 1
                hit = _deep.get(s)
                if hit is None or time.time() - hit["ts"] > DEEP_TTL * 0.75:
                    await _warm_deep(s)
            _warm_state["last"] = time.strftime("%H:%M:%S")
        except Exception as ex:
            _warm_state["errors"] += 1
            print(f"[warmer] {ex!r}", flush=True)
        await asyncio.sleep(max(5.0, WARM_TICK - (time.time() - t0)))


@app.on_event("startup")
async def _start_warmer() -> None:
    asyncio.create_task(_warmer())


@app.get("/api/status")
def status() -> dict:
    cache = ParquetCache()
    asof = {}
    for tf in TIMEFRAMES:
        last = None
        for s in SYMBOLS[:30]:                       # sample for speed
            df = cache.read(s, tf)
            if df is not None and len(df):
                d = str(df.index[-1].date())
                last = d if last is None or d > last else last
        asof[tf] = last
    return {"timeframes": TIMEFRAMES, "asof": asof, "symbols": len(SYMBOLS),
            "warmer": {"last_tick": _warm_state["last"], "errors": _warm_state["errors"],
                       "deep_cached": len(_deep),
                       "matrix_age_s": round(time.time() - _mx["ts"]) if _mx["data"] else None}}
