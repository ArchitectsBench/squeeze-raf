"""Ops/failure push notifications via ntfy.sh (topic from NTFY_TOPIC in .env).

For FAILURES and ops events — distinct from squeeze_scan's setup alerts. Never
raises: alerting must not take down the thing it is alerting about.
"""

from __future__ import annotations

import os

import httpx

from data.base import load_dotenv


def _header_safe(s: str) -> str:
    """HTTP headers are ASCII-only — an em dash in a card title made httpx raise
    BEFORE the network call, and 9 trade cards died silently (2026-07-09/10).
    Transliterate the common symbols, strip the rest. Body stays full UTF-8."""
    for a, b in (("—", "-"), ("–", "-"), ("·", "|"), ("▲", "UP"), ("▼", "DOWN")):
        s = s.replace(a, b)
    return s.encode("ascii", "replace").decode()


def push(message: str, title: str = "squeeze-raf OPS", priority: str = "high") -> bool:
    load_dotenv()
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("[notify] push skipped: no NTFY_TOPIC", flush=True)
        return False
    try:
        r = httpx.post(f"https://ntfy.sh/{topic}", content=message.encode(),
                       headers={"Title": _header_safe(title), "Priority": priority},
                       timeout=10)
        if r.status_code >= 300:
            print(f"[notify] push FAILED: HTTP {r.status_code} {r.text[:120]}", flush=True)
            return False
        return True
    except Exception as ex:
        print(f"[notify] push FAILED: {ex!r}", flush=True)
        return False
