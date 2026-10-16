// Live connection from the Agent Console to the backend (WebSocket /ws/console).
import { useEffect, useReducer, useRef, useState } from "react";
import type { Call, Line, Settings } from "./api";

type Warning = { message: string; at: number };
type State = { calls: Record<string, Call>; settings: Settings; warnings: Record<string, Warning> };

type Event =
  | { type: "snapshot"; calls: Call[]; settings: Settings }
  | { type: "call.updated"; call: Call }
  | { type: "line"; call_id: string; line: Line }
  | { type: "customer.live" | "ai.live"; call_id: string; text: string }
  | { type: "settings"; settings: Settings }
  | { type: "warning"; call_id: string; message: string };

function reducer(state: State, ev: Event): State {
  switch (ev.type) {
    case "snapshot":
      return {
        ...state,
        settings: ev.settings,
        calls: Object.fromEntries(ev.calls.map((c) => [c.call_id, c])),
      };
    case "call.updated":
      return { ...state, calls: { ...state.calls, [ev.call.call_id]: ev.call } };
    case "line": {
      const c = state.calls[ev.call_id];
      if (!c || c.lines.some((l) => l.index === ev.line.index)) return state;
      const live = { ...c.live };
      delete live[ev.line.speaker];
      return {
        ...state,
        calls: { ...state.calls, [c.call_id]: { ...c, lines: [...c.lines, ev.line], live } },
      };
    }
    case "customer.live":
    case "ai.live": {
      const c = state.calls[ev.call_id];
      if (!c) return state;
      const who = ev.type === "customer.live" ? "customer" : "ai";
      return {
        ...state,
        calls: { ...state.calls, [c.call_id]: { ...c, live: { ...c.live, [who]: ev.text } } },
      };
    }
    case "settings":
      return { ...state, settings: ev.settings };
    case "warning":
      return {
        ...state,
        warnings: { ...state.warnings, [ev.call_id]: { message: ev.message, at: Date.now() / 1000 } },
      };
    default:
      return state;
  }
}

export function useConsole() {
  const [state, dispatch] = useReducer(reducer, {
    calls: {},
    settings: { auto_answer: true, ring_seconds: 5 },
    warnings: {},
  });
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closed = false;
    let retry: number | undefined;
    let ping: number | undefined;

    const connect = () => {
      // Connect straight to the backend (port 8000), not through the Vite proxy:
      // the proxy logs a noisy ECONNABORTED every time a page reloads or a socket closes.
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const backend = import.meta.env.VITE_BACKEND_WS ?? `${proto}://${location.hostname}:8000`;
      const ws = new WebSocket(`${backend}/ws/console`);
      wsRef.current = ws;
      ws.onopen = () => {
        setConnected(true);
        ping = window.setInterval(() => ws.readyState === 1 && ws.send("ping"), 20000);
      };
      ws.onmessage = (m) => dispatch(JSON.parse(m.data) as Event);
      ws.onclose = () => {
        setConnected(false);
        window.clearInterval(ping);
        if (!closed) retry = window.setTimeout(connect, 1500); // auto-reconnect
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearInterval(ping);
      wsRef.current?.close();
    };
  }, []);

  const calls = Object.values(state.calls).sort((a, b) => b.created_at - a.created_at);
  return { calls, settings: state.settings, warnings: state.warnings, connected };
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
