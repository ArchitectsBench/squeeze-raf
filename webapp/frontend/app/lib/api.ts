// Use 127.0.0.1 (not "localhost") so the browser hits IPv4 — uvicorn binds 127.0.0.1,
// and "localhost" can resolve to IPv6 ::1 where nothing is listening (fetch would fail).
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

export type Cell = {
  price: number;
  state: number;
  coil: number;
  dir: "UP" | "DOWN" | "—";
  mom: number;
  mom_building: boolean;
  tier: "ALERT" | "WATCH" | null;
  fired_recent: boolean;
};

export type Ticker = {
  symbol: string;
  cells: Record<string, Cell>;
  conf_score: number;
  conf_dir: "UP" | "DOWN";
  conf_n: number;
};

export type Matrix = {
  timeframes: string[];
  asof: Record<string, string>;
  tickers: Ticker[];
  computed_at: string;
};

export type Bar = {
  t: string; o: number; h: number; l: number; c: number;
  v: number; state: number; mom: number; ma: number | null;
  rvol?: number | null;
};

export async function fetchMatrix(refresh = false): Promise<Matrix> {
  const r = await fetch(`${API_BASE}/api/matrix${refresh ? "?refresh=true" : ""}`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`matrix ${r.status}`);
  return r.json();
}

export async function fetchChart(symbol: string, tf: string): Promise<{ bars: Bar[] }> {
  const r = await fetch(`${API_BASE}/api/chart?symbol=${symbol}&tf=${tf}`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`chart ${r.status}`);
  return r.json();
}

export type Sentiment = "positive" | "negative" | "neutral";
export type NewsItem = {
  title: string; publisher: string; published: string;
  url: string; sentiment: Sentiment | null; reasoning: string | null;
};
export type News = {
  symbol: string; count: number; sentiment: Sentiment;
  breakdown: Record<Sentiment, number>; items: NewsItem[];
};

export async function fetchNewsSummary(
  symbols: string[]
): Promise<Record<string, { count: number; sentiment: Sentiment }>> {
  if (!symbols.length) return {};
  const r = await fetch(`${API_BASE}/api/news_summary?symbols=${symbols.join(",")}`, {
    cache: "no-store",
  });
  if (!r.ok) return {};
  return r.json();
}

export async function fetchNews(symbol: string): Promise<News> {
  const r = await fetch(`${API_BASE}/api/news?symbol=${symbol}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`news ${r.status}`);
  return r.json();
}

export type Tape = {
  score: number;
  label: "RISK-ON" | "NEUTRAL" | "RISK-OFF";
  asof: string;
  components: {
    breadth: { score: number; pct_above_50: number; pct_above_200: number;
      mcclellan_osc: number; ad_slope: number; net_high_low: number };
    volatility: { score: number; vix: number; vix3m: number; term_ratio: number; regime: string };
    credit: { score: number; hy_spread: number; hy_rising_20d: boolean; curve_2s10s: number };
  };
  weights: Record<string, number>;
};

export async function fetchTape(): Promise<Tape> {
  const r = await fetch(`${API_BASE}/api/tape`, { cache: "no-store" });
  if (!r.ok) throw new Error(`tape ${r.status}`);
  return r.json();
}

export type TapeHistPoint = { date: string; tape: number; breadth: number; volatility: number; credit: number };
export async function fetchTapeHistory(days = 120): Promise<TapeHistPoint[]> {
  const r = await fetch(`${API_BASE}/api/tape/history?days=${days}`, { cache: "no-store" });
  if (!r.ok) return [];
  return (await r.json()).history;
}

export type Intraday = {
  available: boolean; asof?: string; pct_up?: number; adv?: number; dec?: number;
  add_proxy?: number; trin_proxy?: number | null; score?: number; label?: string;
  tick_series?: { t: string; v: number }[];
};
export async function fetchIntraday(): Promise<Intraday> {
  const r = await fetch(`${API_BASE}/api/internals/intraday`, { cache: "no-store" });
  if (!r.ok) return { available: false };
  return r.json();
}

export type Verdict = { call: "GO" | "CAUTION" | "AVOID"; conviction: number; direction: string; reasons: string[] };
export type Decisions = { tape: number; tape_label: string; intraday: number | null; verdicts: Record<string, Verdict> };
export async function fetchDecisions(): Promise<Decisions | null> {
  const r = await fetch(`${API_BASE}/api/decisions`, { cache: "no-store" });
  if (!r.ok) return null;
  return r.json();
}
export async function fetchDecision(symbol: string): Promise<any> {
  const r = await fetch(`${API_BASE}/api/decision?symbol=${symbol}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`decision ${r.status}`);
  return r.json();
}

export type TradeRow = {
  id: number; symbol: string; asset: string; src: string | null; conv: number | null;
  side: string; qty: number; state: string; fill: number; ts?: string | null;
  hold_days?: number | null; tp?: number | null; sl?: number | null;
  cur?: number; "pnl$"?: number; realized?: boolean;
};
export type Trades = {
  account: string; sandbox: boolean; rows: TradeRow[];
  realized: number; unrealized: number; total: number; computed_at: string;
};
export async function fetchTrades(refresh = false): Promise<Trades> {
  const r = await fetch(`${API_BASE}/api/trades${refresh ? "?refresh=true" : ""}`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`trades ${r.status}`);
  return r.json();
}

export type TradePlan = {
  symbol: string; direction?: string; exp?: string; error?: string;
  inputs?: Record<string, any>;
  recommended?: Record<string, any>;
  alternatives?: Record<string, any>[];
  gex?: Record<string, any>;
};
export async function fetchTradePlan(symbol: string): Promise<TradePlan> {
  const r = await fetch(`${API_BASE}/api/trade_plan?symbol=${symbol}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`trade_plan ${r.status}`);
  return r.json();
}
