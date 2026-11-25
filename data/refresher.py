"""Background data refresher for the squeeze screener (Phase 2).

    uv run python -m data.refresher              # daemon: refresh each TF on its cadence
    uv run python -m data.refresher --once       # one forced pass of all base TFs, then exit
    uv run python -m data.refresher --once --limit 3   # quick test on a few symbols

Pulls only the BASE timeframes (5m/15m/30m/1d) INCREMENTALLY — fetches just the recent
`lookback_days` and merges into cached history — then re-derives the dependent timeframes
locally (no API). RTH-aware. The web API reads the cache, so refreshes appear automatically.
Cadences live in config/refresh.yaml. Respects the adapter's calls-per-min throttle.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from data.cache import ParquetCache
from data.polygon_adapter import PolygonAdapter
from data.resample import resample_intraday, resample_multiday

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config" / "refresh.yaml"
HEARTBEAT = ROOT / "logs" / "refresher_heartbeat"
ET = ZoneInfo("America/New_York")
ALERT_COOLDOWN_S = 3600  # at most one failure alert per tf per hour (5-min cycles)


def _now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


def market_open(now_et: datetime) -> bool:
    return now_et.weekday() < 5 and dtime(9, 30) <= now_et.timetz().replace(tzinfo=None) < dtime(16, 0)


def _load_symbols(which: str) -> list[str]:
    if which == "common_stocks" and (ROOT / "config" / "common_stocks.json").exists():
        return json.loads((ROOT / "config" / "common_stocks.json").read_text())
    return json.loads((ROOT / "config" / "liquid_universe.json").read_text())


def incremental(cache: ParquetCache, ad: PolygonAdapter, sym: str, tf: str,
                lookback_days: int, end: str) -> bool:
    """Fetch the recent window for one symbol/tf and merge into cached history."""
    start = (pd.Timestamp(end) - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    new = ad.fetch(sym, tf, start, end)
    if new is None or len(new) == 0:
        return False
    old = cache.read(sym, tf)
    if old is not None and len(old):
        comb = pd.concat([old, new])
        comb = comb[~comb.index.duplicated(keep="last")].sort_index()
    else:
        comb = new
    cache.write(sym, tf, comb, vendor="polygon")
    return True


def derive(cache: ParquetCache, syms: list[str], base_tf: str, targets: list[str]) -> None:
    fn = resample_multiday if base_tf == "1d" else resample_intraday
    for s in syms:
        b = cache.read(s, base_tf)
        if b is None or len(b) == 0:
            continue
        for tgt in targets:
            try:
                out = fn(b, tgt)
                if len(out):
                    cache.write(s, tgt, out, vendor="derived")
            except Exception:
                pass


def refresh_tf(cache, ad, syms, tf, cfg, derive_cfg) -> int:
    # The adapter memoizes fetches per (symbol, …, start, end). In a long-running daemon
    # `end` is constant all session, so without clearing, the FIRST fetch of the day is
    # cached and later cycles never pull new intraday bars. Clear it each pass for fresh data.
    ad._base_cache.clear()
    end = _now_et().strftime("%Y-%m-%d")
    n = fails = 0
    for s in syms:
        try:
            if incremental(cache, ad, s, tf, cfg["lookback_days"], end):
                n += 1
        except Exception:
            fails += 1
    if tf in derive_cfg:
        derive(cache, syms, tf, derive_cfg[tf])
    return n, fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="screener data refresher")
    ap.add_argument("--once", action="store_true", help="one forced pass of all base TFs, then exit")
    ap.add_argument("--limit", type=int, default=0, help="limit #symbols (testing)")
    args = ap.parse_args(argv)
    try:
        from data.base import load_dotenv
        load_dotenv()
    except Exception:
        pass

    cfg = yaml.safe_load(CFG.read_text())
    syms = _load_symbols(cfg.get("universe", "common_stocks"))
    if args.limit:
        syms = syms[: args.limit]
    cache, ad = ParquetCache(), PolygonAdapter()
    derive_cfg = cfg.get("derive", {})

    if args.once:
        for tf, c in cfg["base"].items():
            n, fails = refresh_tf(cache, ad, syms, tf, c, derive_cfg)
            print(f"[once] {tf}: refreshed {n}/{len(syms)} ({fails} failed)", flush=True)
        return 0

    from reporting.notify import push

    print(f"refresher daemon — {len(syms)} symbols | cadences: "
          f"{ {tf: c['interval_min'] for tf, c in cfg['base'].items()} }", flush=True)
    last: dict[str, float] = {tf: 0.0 for tf in cfg["base"]}
    last_alert: dict[str, float] = {}

    def alert(key: str, msg: str) -> None:
        if time.time() - last_alert.get(key, 0.0) >= ALERT_COOLDOWN_S:
            push(msg)
            last_alert[key] = time.time()

    try:
        while True:
            HEARTBEAT.parent.mkdir(exist_ok=True)
            HEARTBEAT.write_text(datetime.now(timezone.utc).isoformat())
            net = _now_et()
            for tf, c in cfg["base"].items():
                if time.time() - last[tf] < c["interval_min"] * 60:
                    continue
                if c.get("rth_only") and not market_open(net):
                    continue
                if c.get("after_close") and net.timetz().replace(tzinfo=None) < dtime(16, 5):
                    continue
                try:
                    n, fails = refresh_tf(cache, ad, syms, tf, c, derive_cfg)
                except Exception as ex:
                    alert(f"tf:{tf}", f"refresher: {tf} pass CRASHED: {ex!r}")
                    last[tf] = time.time()   # don't hot-loop a broken pass
                    continue
                last[tf] = time.time()
                print(f"[{net:%H:%M}] {tf}: refreshed {n}/{len(syms)} ({fails} failed)", flush=True)
                if fails > len(syms) // 2:
                    alert(f"fails:{tf}", f"refresher: {tf} — {fails}/{len(syms)} symbols "
                                         "FAILED this pass (rate limit / vendor outage?)")
            time.sleep(30)
    except Exception as ex:
        push(f"refresher daemon CRASHED: {ex!r} — supervisor should restart it",
             priority="urgent")
        raise


if __name__ == "__main__":
    sys.exit(main())
