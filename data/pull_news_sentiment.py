"""Build a point-in-time DAILY sentiment timeline per ticker from Polygon news.

    uv run python -m data.pull_news_sentiment --start 2024-07-01

Polygon's news `insights` (per-ticker sentiment) exist only from ~Aug 2024. For each
universe ticker we page through its news from --start and aggregate per CALENDAR DAY:
{ticker: {YYYY-MM-DD: [pos, neg, neu]}} -> data/news_sentiment.json. Used by the
catalyst backtest (validation.catalyst_backtest). One-time-ish; re-run to extend.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

from data.base import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "news_sentiment.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--max-pages", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    load_dotenv()
    key = os.environ["POLYGON_API_KEY"]
    syms = json.loads((ROOT / "config" / "common_stocks.json").read_text())
    if args.limit:
        syms = syms[: args.limit]

    out: dict[str, dict] = {}
    with httpx.Client(timeout=30) as c:
        for n, t in enumerate(syms):
            daily: dict[str, list] = {}
            url = "https://api.polygon.io/v2/reference/news"
            params = {"ticker": t, "published_utc.gte": args.start, "limit": 1000,
                      "order": "asc", "apiKey": key}
            pages = 0
            while url and pages < args.max_pages:
                try:
                    r = c.get(url, params=params if pages == 0 else {"apiKey": key})
                    j = r.json()
                except Exception:
                    break
                for a in j.get("results", []):
                    ins = next((i for i in (a.get("insights") or []) if i.get("ticker") == t), None)
                    if not ins:
                        continue
                    day = (a.get("published_utc") or "")[:10]
                    if not day:
                        continue
                    pn = daily.setdefault(day, [0, 0, 0])
                    s = ins.get("sentiment")
                    pn[0 if s == "positive" else 1 if s == "negative" else 2] += 1
                url = j.get("next_url")
                pages += 1
            if daily:
                out[t] = daily
            if n % 25 == 0:
                print(f"  {n}/{len(syms)} … {t} ({len(daily)} days)", flush=True)
                time.sleep(0.5)
    OUT.write_text(json.dumps(out))
    print(f"wrote {OUT.name}: {len(out)} tickers with sentiment", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
