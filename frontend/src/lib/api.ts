// Types + tiny fetch helpers for the FastAPI backend (proxied by Vite).

export type Speaker = "customer" | "ai";

export interface Line {
  index: number;
  speaker: Speaker;
  text: string;
  t: number; // seconds from call start
  interrupted?: boolean;
}

export type CallStatus =
  | "ringing"
  | "connecting"
  | "active"
  | "wrapping_up"
  | "ended"
  | "missed"
  | "declined";

export interface Evidence {
  value: boolean | null;
  evidence: string | null;
  line: number | null;
}

export interface Summary {
  short_summary?: string | null;
  call_reason?: string;
  caller?: { name?: string | null; verified?: boolean; verification_method?: string | null };
  card?: { last_four?: string | null; block_requested?: boolean; block_confirmed_by_readback?: boolean };
  disputed_transactions?: {
    amount?: number | null;
    currency?: string | null;
    merchant_or_payee?: string | null;
    when?: string | null;
  }[];
  how_it_happened?: string | null;
  liability_facts?: Record<string, Evidence>;
  vulnerability_cues?: { cue: string; evidence: string; line: number }[];
  actions_taken?: string[];
  missing_information?: string[];
  customer_sentiment?: string;
  next_steps?: string[];
  error?: string | null;
}

export interface Call {
  call_id: string;
  room: string;
  caller_name: string;
  caller_number: string;
  status: CallStatus;
  created_at: number;
  answered_at: number | null;
  ended_at: number | null;
  agent_state: string | null;
  user_state: string | null;
  lines: Line[];
  live: { customer?: string; ai?: string };
  summary: Summary | null;
  file: string | null;
  error: string | null;
}

export interface Settings {
  auto_answer: boolean;
  ring_seconds: number;
}

export interface HistoryItem {
  call_id: string;
  caller: { name: string | null; number: string | null } | null;
  started_at: string | null;
  duration_seconds: number | null;
  short_summary: string | null;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  startCall: (caller_name: string, caller_number: string) =>
    req<{ call: Call; livekit_url: string; token: string }>("/api/calls", {
      method: "POST",
      body: JSON.stringify({ caller_name, caller_number }),
    }),
  answer: (id: string) => req<Call>(`/api/calls/${id}/answer`, { method: "POST" }),
  decline: (id: string) => req<Call>(`/api/calls/${id}/decline`, { method: "POST" }),
  hangup: (id: string) => req<Call>(`/api/calls/${id}/hangup`, { method: "POST" }),
  record: (id: string) => req<any>(`/api/calls/${id}/record`),
  history: () => req<HistoryItem[]>("/api/history"),
  settings: (s: Partial<Settings>) =>
    req<Settings>("/api/settings", { method: "POST", body: JSON.stringify(s) }),
};

/** Turn a saved JSON record (from disk) into a Call object the console can display. */
export function recordToCall(r: any): Call {
  const start = r.started_at ? Date.parse(r.started_at) / 1000 : 0;
  const end = r.ended_at ? Date.parse(r.ended_at) / 1000 : null;
  return {
    call_id: r.call_id,
    room: "",
    caller_name: r.caller?.name ?? "Unknown",
    caller_number: r.caller?.number ?? "",
    status: "ended",
    created_at: start,
    answered_at: start,
    ended_at: end,
    agent_state: null,
    user_state: null,
    lines: r.transcript ?? [],
    live: {},
    summary: r.summary ?? null,
    file: `${r.call_id}.json`,
    error: null,
  };
}

export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
