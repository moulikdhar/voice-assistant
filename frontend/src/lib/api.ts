// Types + tiny fetch helpers for the FastAPI backend (proxied by Vite under /api).

export type Speaker = "customer" | "ai" | "specialist";

export interface Line {
  index: number;
  speaker: Speaker;
  text: string;
  t: number;
  phase?: "ai" | "interview";
  interrupted?: boolean;
}

export type CallStatus = "ringing" | "connecting" | "active" | "wrapping_up" | "ended" | "missed" | "declined";

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
  disputed_transactions?: { amount?: number | null; currency?: string | null; merchant_or_payee?: string | null; when?: string | null }[];
  how_it_happened?: string | null;
  liability_facts?: Record<string, Evidence>;
  vulnerability_cues?: { cue: string; evidence: string; line: number }[];
  actions_taken?: string[];
  missing_information?: string[];
  customer_sentiment?: string;
  next_steps?: string[];
  error?: string | null;
}

export interface Turn { eou: number; llm_ttft: number; tts_ttfb: number; total: number }

export interface Assist {
  checklist?: { id: string; question?: string; status: "answered" | "partial" | "open"; answer?: string | null; evidence?: string | null; source?: string | null }[];
  disclosures?: { id: string; text?: string; done: boolean; evidence?: string | null }[];
  next_question?: string;
  alerts?: string[];
  policy_hint?: string | null;
  updated_at?: string;
}

export interface Call {
  call_id: string;
  room: string;
  caller_name: string;
  caller_number: string;
  status: CallStatus;
  mode: "intake" | "status";
  phase: "ai" | "handover" | "interview";
  created_at: number;
  answered_at: number | null;
  ended_at: number | null;
  customer_id: string | null;
  claim_id: string | null;
  agent_state: string | null;
  user_state: string | null;
  lines: Line[];
  live: Partial<Record<Speaker, string>>;
  verification: { otp_sent?: boolean; verified?: boolean; attempts?: number; method?: string; mobile_ending?: string };
  card_actions: { action: string; card_last4: string; time: string; confirmed_by_readback: boolean }[];
  pending: { id: string; kind: string; read_back: string }[];
  summary: Summary | null;
  assist: Assist | null;
  specialist: { mode: "human" | "simulated"; name: string; status: string } | null;
  handover_reason?: string | null;
  metrics: { turns?: Turn[]; usage?: Record<string, number>; cost_usd?: { total: number } };
  audio_file: string | null;
  batch_transcript: { segments?: { speaker: number | string | null; start: number; end: number; text: string }[]; error?: string } | null;
  file: string | null;
  error: string | null;
}

export interface Txn { id: string; time: string; merchant: string; amount: number; currency: string; card_last4: string; channel: string; direction?: "debit" | "credit"; status?: string }

export interface Brief {
  decision_ready_summary?: string;
  timeline?: { time: string; event: string; source: string }[];
  key_facts?: { fact: string; value: string; evidence?: { call_id?: string; quote?: string } }[];
  conflicts?: { title: string; detail: string; statements?: { source: string; quote: string }[]; severity: string }[];
  qa_scorecard?: { call_id: string; criterion_id: string; criterion?: string; met: boolean | null; evidence?: string | null }[];
  qa_scores?: Record<string, { met: number; missed: number; score_pct: number | null }>;
  repeated_questions?: string[];
  open_questions?: string[];
  recommended_next_steps?: string[];
  generated_at?: string;
  error?: string;
}

export interface Claim {
  id: string;
  reference: string;
  customer_id: string;
  customer_name: string;
  phone: string;
  jurisdiction: string;
  status: "open" | "interview" | "investigation" | "decided" | "refunded";
  steps: { report: string; interview: string; investigate: string; followup: string };
  created_at: string;
  updated_at?: string;
  opened_at: string;
  deadline_at: string;
  clock: { opened_at: string; deadline_at: string; business_days_left: number; rule: string; breached: boolean };
  disputed_transactions: Txn[];
  total_disputed: number;
  currency: string;
  facts: Record<string, unknown>;
  calls: string[];
  intake_summary: Summary | null;
  assist: Assist | null;
  brief: Brief | null;
  decision: { outcome: string; notes: string; analyst: string; time: string } | null;
  refund?: { txn_id: string; amount: number; time: string; account_last4: string; new_balance: number } | null;
  followups: { call_id: string; time: string; summary: string | null }[];
  // only on the detail endpoint
  call_details?: Call[];
  audit?: Audit[];
  security_log?: { time: string; event: string; detail: string }[];
  metrics?: { turns: number; p50: number | null; p95: number | null; cost_usd: number };
}

export interface Audit { id: string; time: string; actor: string; action: string; detail: string; claim_id: string | null; call_id: string | null }
export interface Sms { id: string; phone: string; text: string; time: string }
export interface Settings { auto_answer: boolean; auto_specialist?: boolean; ring_seconds: number; jurisdiction?: string }

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) =>
  req<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  startCall: (caller_name: string, caller_number: string) =>
    post<{ call: Call; livekit_url: string; token: string }>("/api/calls", { caller_name, caller_number }),
  answer: (id: string) => post<Call>(`/api/calls/${id}/answer`),
  decline: (id: string) => post<Call>(`/api/calls/${id}/decline`),
  hangup: (id: string) => post<Call>(`/api/calls/${id}/hangup`),
  specialistJoin: (id: string) => post<{ livekit_url: string; token: string; call: Call }>(`/api/calls/${id}/specialist`),
  simulateSpecialist: (id: string) => post<Call>(`/api/calls/${id}/simulate-specialist`),
  endInterview: (id: string) => post<Call>(`/api/calls/${id}/end-interview`),
  claim: (id: string) => req<Claim>(`/api/claims/${id}`),
  brief: (id: string) => post<{ ok: boolean }>(`/api/claims/${id}/brief`),
  decide: (id: string, outcome: string, notes: string) => post<Claim>(`/api/claims/${id}/decision`, { outcome, notes }),
  settings: (s: Partial<Settings>) => post<Settings>("/api/settings", s),
  reset: () => post<{ ok: boolean }>("/api/admin/reset"),
};

export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function money(n?: number | null, cur = "INR"): string {
  if (n == null) return "—";
  return `${cur === "INR" ? "₹" : cur + " "}${n.toLocaleString("en-IN")}`;
}

export const digitsOnly = (s: string) => s.replace(/\D/g, "").slice(-10);
