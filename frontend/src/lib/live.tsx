// ONE live connection (WebSocket /ws/console) shared by every screen: phone, console, desk, investigator.
import { createContext, useContext, useEffect, useMemo, useReducer, useState, type ReactNode } from "react";
import type { Assist, Audit, Call, Claim, Line, Settings, Sms, Speaker } from "./api";

interface State {
  calls: Record<string, Call>;
  claims: Record<string, Claim>;
  sms: Sms[];
  audit: Audit[];
  settings: Settings;
  warnings: Record<string, { message: string; at: number }>;
  briefProgress: Record<string, string>;
  handovers: Record<string, number>; // call_id -> time requested
  resetCount: number;
}

type Ev =
  | { type: "snapshot"; calls: Call[]; claims: Claim[]; sms: Sms[]; settings: Settings }
  | { type: "call.updated"; call: Call }
  | { type: "claim.updated"; claim: Claim }
  | { type: "line"; call_id: string; line: Line }
  | { type: "live"; call_id: string; speaker: Speaker; text: string }
  | { type: "sms"; sms: Sms }
  | { type: "audit"; entry: Audit }
  | { type: "assist.updated"; call_id: string; assist: Assist }
  | { type: "handover"; call_id: string }
  | { type: "handover.ready"; call_id: string }
  | { type: "brief.progress"; claim_id: string; stage: string }
  | { type: "settings"; settings: Settings }
  | { type: "warning"; call_id: string; message: string }
  | { type: "reset" };

const initial: State = {
  calls: {}, claims: {}, sms: [], audit: [], settings: { auto_answer: true, auto_specialist: true, ring_seconds: 5 },
  warnings: {}, briefProgress: {}, handovers: {}, resetCount: 0,
};

function patchCall(s: State, id: string, f: (c: Call) => Call): State {
  const c = s.calls[id];
  return c ? { ...s, calls: { ...s.calls, [id]: f(c) } } : s;
}

function reducer(s: State, ev: Ev): State {
  switch (ev.type) {
    case "snapshot":
      return {
        ...s,
        settings: ev.settings,
        calls: Object.fromEntries(ev.calls.map((c) => [c.call_id, c])),
        claims: Object.fromEntries(ev.claims.map((c) => [c.id, c])),
        sms: ev.sms,
      };
    case "call.updated":
      return { ...s, calls: { ...s.calls, [ev.call.call_id]: ev.call } };
    case "claim.updated": {
      const prev = s.claims[ev.claim.id];
      return { ...s, claims: { ...s.claims, [ev.claim.id]: { ...prev, ...ev.claim } } };
    }
    case "line":
      return patchCall(s, ev.call_id, (c) => {
        if (c.lines.some((l) => l.index === ev.line.index)) return c;
        const live = { ...c.live };
        delete live[ev.line.speaker];
        return { ...c, lines: [...c.lines, ev.line], live };
      });
    case "live":
      return patchCall(s, ev.call_id, (c) => ({ ...c, live: { ...c.live, [ev.speaker]: ev.text } }));
    case "assist.updated":
      return patchCall(s, ev.call_id, (c) => ({ ...c, assist: ev.assist }));
    case "sms":
      return { ...s, sms: [...s.sms, ev.sms] };
    case "audit":
      return { ...s, audit: [...s.audit.slice(-200), ev.entry] };
    case "handover":
      return { ...s, handovers: { ...s.handovers, [ev.call_id]: Date.now() } };
    case "brief.progress":
      return { ...s, briefProgress: { ...s.briefProgress, [ev.claim_id]: ev.stage } };
    case "settings":
      return { ...s, settings: ev.settings };
    case "warning":
      return { ...s, warnings: { ...s.warnings, [ev.call_id]: { message: ev.message, at: Date.now() / 1000 } } };
    case "reset":
      return { ...initial, settings: s.settings, resetCount: s.resetCount + 1 };
    default:
      return s;
  }
}

const Ctx = createContext<{ state: State; connected: boolean; callList: Call[]; claimList: Claim[] } | null>(null);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initial);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let closed = false;
    let retry: number | undefined;
    let ping: number | undefined;
    let ws: WebSocket | null = null;
    const connect = () => {
      // Straight to the backend (port 8000), not through the Vite proxy (avoids proxy noise).
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const base = import.meta.env.VITE_BACKEND_WS ?? `${proto}://${location.hostname}:8000`;
      ws = new WebSocket(`${base}/ws/console`);
      ws.onopen = () => {
        setConnected(true);
        ping = window.setInterval(() => ws?.readyState === 1 && ws.send("ping"), 20000);
      };
      ws.onmessage = (m) => dispatch(JSON.parse(m.data) as Ev);
      ws.onclose = () => {
        window.clearInterval(ping);
        if (closed) return; // closed on purpose (unmount / StrictMode): not a disconnect
        setConnected(false);
        retry = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearInterval(ping);
      ws?.close();
    };
  }, []);

  // sort once per change (not in every consumer on every render)
  const callList = useMemo(() => Object.values(state.calls).sort((a, b) => b.created_at - a.created_at), [state.calls]);
  const claimList = useMemo(() => Object.values(state.claims).sort((a, b) => (a.created_at < b.created_at ? 1 : -1)), [state.claims]);
  return <Ctx.Provider value={{ state, connected, callList, claimList }}>{children}</Ctx.Provider>;
}

export function useLive() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLive outside LiveProvider");
  const { state, connected, callList, claimList } = v;
  return { ...state, callList, claimList, connected };
}

/** Re-render every `ms` (for live timers). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now() / 1000), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}
