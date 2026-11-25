"""Tradier client for PAPER (sandbox) forward-testing (P9 --paper).

Sandbox only by default (TRADIER_BASE defaults to the sandbox host). Used to:
- verify connectivity (account balances),
- pull live option chains (authoritative OCC symbols + greeks) for the recs,
- place PAPER equity / single-leg option / vertical orders.

Every order supports `preview=True` (Tradier validates without placing) — the safe
default. Real placement requires `preview=False` explicitly. Credentials come from
.env (TRADIER_TOKEN / TRADIER_ACCOUNT / TRADIER_BASE). NEVER point this at a live
token without intent — this project's signals are NOT graduated.
"""

from __future__ import annotations

import os

import httpx

from data.base import load_dotenv

SANDBOX = "https://sandbox.tradier.com"
PROD = "https://api.tradier.com"


class TradierError(RuntimeError):
    pass


class TradierData:
    """READ-ONLY market-data client. Uses TRADIER_DATA_TOKEN (a funded production
    account's token -> REAL-TIME quotes) when present, else falls back to the
    sandbox token (15-min delayed). Deliberately has NO order methods and never
    touches an account id — it cannot trade, so a live token here is safe."""

    def __init__(self):
        load_dotenv()
        tok = os.environ.get("TRADIER_DATA_TOKEN") or os.environ.get("TRADIER_PROD_TOKEN")
        self.realtime = bool(tok)
        self.token = tok or os.environ.get("TRADIER_TOKEN")
        self.base = PROD if tok else os.environ.get("TRADIER_BASE", SANDBOX)
        if not self.token:
            raise TradierError("no TRADIER_DATA_TOKEN or TRADIER_TOKEN in .env")
        self._h = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def quote(self, symbols) -> dict:
        syms = ",".join(symbols) if not isinstance(symbols, str) else symbols
        r = httpx.get(f"{self.base}/v1/markets/quotes", headers=self._h,
                      params={"symbols": syms}, timeout=15)
        if r.status_code >= 400:
            raise TradierError(f"quotes -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def last(self, symbol) -> float | None:
        q = self.quote(symbol).get("quotes", {}).get("quote")
        q = q[0] if isinstance(q, list) else q
        return float(q["last"]) if q and q.get("last") else None

    def expirations(self, symbol) -> list[str]:
        r = httpx.get(f"{self.base}/v1/markets/options/expirations", headers=self._h,
                      params={"symbol": symbol, "includeAllRoots": "true"}, timeout=15)
        if r.status_code >= 400:
            raise TradierError(f"expirations -> {r.status_code}: {r.text[:200]}")
        d = (r.json().get("expirations") or {}).get("date") or []
        return d if isinstance(d, list) else [d]

    def chain(self, symbol, expiration) -> list[dict]:
        r = httpx.get(f"{self.base}/v1/markets/options/chains", headers=self._h,
                      params={"symbol": symbol, "expiration": expiration, "greeks": "true"},
                      timeout=30)
        if r.status_code >= 400:
            raise TradierError(f"chain -> {r.status_code}: {r.text[:200]}")
        opts = (r.json().get("options") or {}).get("option") or []
        return opts if isinstance(opts, list) else [opts]

    def timesales(self, symbol, interval="5min", days=10):
        """Real-time intraday OHLCV bars (RTH only) as a cache-schema DataFrame."""
        import pandas as pd
        start = (pd.Timestamp.now(tz="America/New_York") - pd.Timedelta(days=days))
        r = httpx.get(f"{self.base}/v1/markets/timesales", headers=self._h,
                      params={"symbol": symbol, "interval": interval,
                              "start": start.strftime("%Y-%m-%d %H:%M"),
                              "session_filter": "open"}, timeout=30)
        if r.status_code >= 400:
            raise TradierError(f"timesales -> {r.status_code}: {r.text[:200]}")
        data = (r.json().get("series") or {}).get("data") or []
        if not data:
            return None
        df = pd.DataFrame(data)
        df.index = (pd.to_datetime(df["time"]).dt.tz_localize("America/New_York")
                    .dt.tz_convert("UTC"))
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        df.index.name = "dt"
        return df.sort_index()


class Tradier:
    def __init__(self):
        load_dotenv()
        self.token = os.environ.get("TRADIER_TOKEN")
        self.account = os.environ.get("TRADIER_ACCOUNT")
        self.base = os.environ.get("TRADIER_BASE", SANDBOX)
        if not self.token or not self.account:
            raise TradierError("set TRADIER_TOKEN and TRADIER_ACCOUNT in .env (sandbox)")
        self._h = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    @property
    def is_sandbox(self) -> bool:
        return "sandbox" in self.base

    def _get(self, path, params=None):
        r = httpx.get(self.base + path, headers=self._h, params=params or {}, timeout=30)
        if r.status_code >= 400:
            raise TradierError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def _post(self, path, data):
        r = httpx.post(self.base + path, headers=self._h, data=data, timeout=30)
        if r.status_code >= 400:
            raise TradierError(f"POST {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    # --- read ---
    def balances(self) -> dict:
        return self._get(f"/v1/accounts/{self.account}/balances")

    def quote(self, symbols) -> dict:
        syms = ",".join(symbols) if not isinstance(symbols, str) else symbols
        return self._get("/v1/markets/quotes", {"symbols": syms})

    def expirations(self, symbol) -> list[str]:
        j = self._get("/v1/markets/options/expirations",
                      {"symbol": symbol, "includeAllRoots": "true"})
        d = (j.get("expirations") or {}).get("date") or []
        return d if isinstance(d, list) else [d]

    def chain(self, symbol, expiration) -> list[dict]:
        j = self._get("/v1/markets/options/chains",
                      {"symbol": symbol, "expiration": expiration, "greeks": "true"})
        opts = (j.get("options") or {}).get("option") or []
        return opts if isinstance(opts, list) else [opts]

    # --- orders (paper) ---
    def equity_order(self, symbol, side, quantity, preview=True, otype="market") -> dict:
        data = {"class": "equity", "symbol": symbol, "side": side,
                "quantity": str(int(quantity)), "type": otype, "duration": "day"}
        if preview:
            data["preview"] = "true"
        return self._post(f"/v1/accounts/{self.account}/orders", data)

    def option_order(self, underlying, option_symbol, side, quantity,
                     preview=True, otype="market") -> dict:
        data = {"class": "option", "symbol": underlying, "option_symbol": option_symbol,
                "side": side, "quantity": str(int(quantity)), "type": otype,
                "duration": "day"}
        if preview:
            data["preview"] = "true"
        return self._post(f"/v1/accounts/{self.account}/orders", data)

    def equity_stop(self, symbol, side, quantity, stop_price, preview=True) -> dict:
        """Resting GTC stop on an equity position (side = sell / buy_to_cover)."""
        data = {"class": "equity", "symbol": symbol, "side": side,
                "quantity": str(int(quantity)), "type": "stop",
                "stop": f"{stop_price:.2f}", "duration": "gtc"}
        if preview:
            data["preview"] = "true"
        return self._post(f"/v1/accounts/{self.account}/orders", data)

    def option_stop(self, underlying, option_symbol, quantity, stop_price, preview=True) -> dict:
        """Resting GTC stop (sell_to_close) on a long option position."""
        data = {"class": "option", "symbol": underlying, "option_symbol": option_symbol,
                "side": "sell_to_close", "quantity": str(int(quantity)), "type": "stop",
                "stop": f"{stop_price:.2f}", "duration": "gtc"}
        if preview:
            data["preview"] = "true"
        return self._post(f"/v1/accounts/{self.account}/orders", data)

    def cancel(self, order_id) -> dict:
        """Cancel one of OUR orders by id (shared-account safe: only ids we pass)."""
        r = httpx.delete(f"{self.base}/v1/accounts/{self.account}/orders/{order_id}",
                         headers=self._h, timeout=30)
        if r.status_code >= 400:
            raise TradierError(f"DELETE order {order_id} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def oco_equity(self, symbol, close_side, qty, limit_price, stop_price,
                   duration="gtc", preview=True) -> dict:
        """OCO bracket on an EXISTING equity position: a take-profit LIMIT and a
        stop-loss STOP that cancel each other. close_side = sell (for a long) or
        buy_to_cover (for a short)."""
        data = {
            "class": "oco", "duration": duration,
            "symbol[0]": symbol, "side[0]": close_side, "quantity[0]": str(int(qty)),
            "type[0]": "limit", "price[0]": f"{limit_price:.2f}",
            "symbol[1]": symbol, "side[1]": close_side, "quantity[1]": str(int(qty)),
            "type[1]": "stop", "stop[1]": f"{stop_price:.2f}",
        }
        if preview:
            data["preview"] = "true"
        return self._post(f"/v1/accounts/{self.account}/orders", data)

    def oto_bracket_equity(self, symbol, side, qty, entry_limit, stop_price, preview=True) -> dict:
        """OTO: LIMIT entry (Tradier forbids a market first leg) that, on fill,
        triggers a GTC stop-loss. side = buy or sell_short."""
        close_side = "sell" if side == "buy" else "buy_to_cover"
        data = {
            "class": "oto",
            "symbol[0]": symbol, "type[0]": "limit", "side[0]": side,
            "quantity[0]": str(int(qty)), "price[0]": f"{entry_limit:.2f}", "duration[0]": "day",
            "symbol[1]": symbol, "type[1]": "stop", "side[1]": close_side,
            "quantity[1]": str(int(qty)), "stop[1]": f"{stop_price:.2f}", "duration[1]": "gtc",
        }
        if preview:
            data["preview"] = "true"
        return self._post(f"/v1/accounts/{self.account}/orders", data)

    def oto_bracket_option(self, underlying, option_symbol, qty, entry_limit, stop_price,
                           preview=True) -> dict:
        """OTO: LIMIT buy_to_open an option, then a GTC stop (sell_to_close) on premium."""
        data = {
            "class": "oto", "symbol": underlying,
            "option_symbol[0]": option_symbol, "type[0]": "limit", "side[0]": "buy_to_open",
            "quantity[0]": str(int(qty)), "price[0]": f"{entry_limit:.2f}", "duration[0]": "day",
            "option_symbol[1]": option_symbol, "type[1]": "stop", "side[1]": "sell_to_close",
            "quantity[1]": str(int(qty)), "stop[1]": f"{stop_price:.2f}", "duration[1]": "gtc",
        }
        if preview:
            data["preview"] = "true"
        return self._post(f"/v1/accounts/{self.account}/orders", data)

    def vertical_order(self, underlying, legs, preview=True, otype="market", price=None) -> dict:
        """legs = [(option_symbol, side, qty), ...] (side e.g. buy_to_open/sell_to_open).
        For a debit/credit spread pass otype='debit'/'credit' and price=net limit per spread."""
        data = {"class": "multileg", "symbol": underlying, "type": otype, "duration": "day"}
        if price is not None:
            data["price"] = f"{float(price):.2f}"
        for i, (osym, side, qty) in enumerate(legs):
            data[f"option_symbol[{i}]"] = osym
            data[f"side[{i}]"] = side
            data[f"quantity[{i}]"] = str(int(qty))
        if preview:
            data["preview"] = "true"
        return self._post(f"/v1/accounts/{self.account}/orders", data)


def nearest_option(chain: list[dict], target_strike: float, kind: str) -> dict | None:
    """Pick the chain option of `kind` ('call'/'put') with strike nearest target.
    Returns the raw Tradier option dict (has 'symbol' = OCC, 'bid','ask','greeks')."""
    cands = [o for o in chain if o.get("option_type") == kind]
    if not cands:
        return None
    return min(cands, key=lambda o: abs(float(o["strike"]) - target_strike))
