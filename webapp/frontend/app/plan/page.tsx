"use client";
import { useEffect, useState } from "react";
import TradePlanView from "../components/TradePlanView";

export default function Plan() {
  const [sym, setSym] = useState("");
  useEffect(() => { setSym(new URLSearchParams(window.location.search).get("symbol") || ""); }, []);
  return (
    <div className="wrap">
      <div className="header">
        <span className="title">📋 Trade Plan {sym}</span>
        <a href="/" className="btn" style={{ textDecoration: "none" }}>← Screener</a>
        <a href="/internals" className="btn" style={{ textDecoration: "none" }}>📊 Internals</a>
      </div>
      {sym ? <TradePlanView symbol={sym} /> : <div className="muted">Add ?symbol=TICKER to the URL.</div>}
    </div>
  );
}
