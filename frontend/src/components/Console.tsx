// "Agent Console": what the bank's support team sees during Step 1 (and Step 4).
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import clsx from "clsx";
import {
  Bot, Brain, CircleDot, Ear, Headset, Loader2, PhoneCall, PhoneIncoming, PhoneOff, User, UserRound, Volume2,
} from "lucide-react";
import { api, fmtDuration, type Call, type Speaker } from "../lib/api";
import { useLive, useNow } from "../lib/live";
import SummaryPanel from "./SummaryPanel";
import LiveClaimPanel from "./LiveClaimPanel";

const LIVE_STATUSES = ["ringing", "connecting", "active", "wrapping_up"];

export default function Console() {
  const { callList, settings, warnings } = useLive();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const newestLive = callList.find((c) => LIVE_STATUSES.includes(c.status));
  useEffect(() => {
    if (newestLive) setSelectedId(newestLive.call_id);
  }, [newestLive?.call_id]);

  const selected = callList.find((c) => c.call_id === selectedId) ?? callList[0] ?? null;
  const ringing = callList.filter((c) => c.status === "ringing");
  const onCall = callList.filter((c) => ["connecting", "active", "wrapping_up"].includes(c.status));
  const recent = callList.filter((c) => !LIVE_STATUSES.includes(c.status)).slice(0, 12);

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden rounded-2xl bg-zinc-900/60 ring-1 ring-zinc-800">
      <header className="flex items-center gap-3 border-b border-zinc-800 px-5 py-3">
        <div className="grid size-8 place-items-center rounded-lg bg-indigo-500/15 text-indigo-300">
          <PhoneCall className="size-4" />
        </div>
        <div>
          <h1 className="text-sm font-semibold">Fraud & Disputes Desk · Agent Console</h1>
          <p className="text-xs text-zinc-500">Voxtral Realtime · Mistral Large + bank tools (MCP) · Voxtral TTS</p>
        </div>
        <label className="ml-auto flex cursor-pointer items-center gap-2 text-xs text-zinc-400"
          title="After the AI transfers the call, the simulated specialist (Oliver) joins by itself">
          <span>Auto specialist</span>
          <button
            onClick={() => void api.settings({ auto_specialist: !settings.auto_specialist }).catch(() => {})}
            className={clsx("relative h-5 w-9 rounded-full transition", settings.auto_specialist ? "bg-violet-500" : "bg-zinc-700")}
          >
            <span className={clsx("absolute top-0.5 size-4 rounded-full bg-white transition-all",
              settings.auto_specialist ? "left-[18px]" : "left-0.5")} />
          </button>
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-400">
          <span>Auto-answer with AI</span>
          <button
            onClick={() => void api.settings({ auto_answer: !settings.auto_answer }).catch(() => {})}
            className={clsx("relative h-5 w-9 rounded-full transition", settings.auto_answer ? "bg-emerald-500" : "bg-zinc-700")}
          >
            <span className={clsx("absolute top-0.5 size-4 rounded-full bg-white transition-all",
              settings.auto_answer ? "left-[18px]" : "left-0.5")} />
          </button>
        </label>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-64 shrink-0 flex-col gap-4 overflow-y-auto border-r border-zinc-800 p-4">
          <Section title="Incoming">
            <AnimatePresence>
              {ringing.map((c) => (
                <IncomingCard key={c.call_id} call={c} autoAnswer={settings.auto_answer} ringSeconds={settings.ring_seconds} />
              ))}
            </AnimatePresence>
            {ringing.length === 0 && <Empty text="No incoming calls" />}
          </Section>
          <Section title="On call">
            {onCall.map((c) => <QueueRow key={c.call_id} call={c} active={selected?.call_id === c.call_id} onClick={() => setSelectedId(c.call_id)} />)}
            {onCall.length === 0 && <Empty text="No active calls" />}
          </Section>
          <Section title="Recent">
            {recent.map((c) => <QueueRow key={c.call_id} call={c} active={selected?.call_id === c.call_id} onClick={() => setSelectedId(c.call_id)} />)}
          </Section>
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          {selected ? <Transcript call={selected} warning={warnings[selected.call_id]} /> : <Placeholder />}
        </main>

        <aside className="w-[340px] shrink-0 space-y-4 overflow-y-auto border-l border-zinc-800 p-4">
          {selected && <LiveClaimPanel call={selected} />}
          {selected && ["ended", "wrapping_up"].includes(selected.status) && <SummaryPanel call={selected} />}
        </aside>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">{title}</h2>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="rounded-xl border border-dashed border-zinc-800 px-3 py-3 text-center text-xs text-zinc-600">{text}</p>;
}

function IncomingCard({ call, autoAnswer, ringSeconds }: { call: Call; autoAnswer: boolean; ringSeconds: number }) {
  const now = useNow(250);
  const ringFor = now - call.created_at;
  return (
    <motion.div layout initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: -20 }}
      className="relative overflow-hidden rounded-xl bg-emerald-500/10 p-3 ring-1 ring-emerald-500/40">
      <div className="flex items-center gap-3">
        <div className="relative grid size-10 place-items-center rounded-full bg-emerald-500/20">
          <span className="absolute inset-0 animate-ping rounded-full bg-emerald-500/30" />
          <PhoneIncoming className="size-5 text-emerald-300" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{call.caller_name}</p>
          <p className="font-mono text-xs text-zinc-400">{call.caller_number}</p>
        </div>
        <span className="ml-auto font-mono text-xs text-emerald-300">{fmtDuration(ringFor)}</span>
      </div>
      {call.mode === "status" && (
        <p className="mt-2 rounded-md bg-amber-500/10 px-2 py-1 text-[11px] text-amber-300">Returning caller · open claim → Status Agent</p>
      )}
      <div className="mt-3 flex gap-2">
        <button onClick={() => void api.answer(call.call_id).catch(() => {})}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-emerald-500 py-1.5 text-xs font-semibold text-black hover:bg-emerald-400">
          <Bot className="size-3.5" /> Answer with AI
        </button>
        <button onClick={() => void api.decline(call.call_id).catch(() => {})}
          className="rounded-lg bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:bg-rose-600 hover:text-white">Decline</button>
      </div>
      {autoAnswer && (
        <div className="absolute bottom-0 left-0 h-0.5 bg-emerald-400" style={{ width: `${Math.min(100, (ringFor / ringSeconds) * 100)}%` }} />
      )}
    </motion.div>
  );
}

const STATUS_STYLE: Record<string, string> = {
  ringing: "bg-emerald-500/15 text-emerald-300",
  connecting: "bg-amber-500/15 text-amber-300",
  active: "bg-emerald-500/15 text-emerald-300",
  wrapping_up: "bg-indigo-500/15 text-indigo-300",
  ended: "bg-zinc-700/50 text-zinc-300",
  missed: "bg-rose-500/15 text-rose-300",
  declined: "bg-rose-500/15 text-rose-300",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={clsx("whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide", STATUS_STYLE[status])}>
      {status.replace("_", " ")}
    </span>
  );
}

function QueueRow({ call, active, onClick }: { call: Call; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className={clsx("w-full rounded-xl px-3 py-2 text-left ring-1 transition hover:bg-zinc-800/60", active ? "bg-zinc-800 ring-zinc-600" : "ring-zinc-800")}>
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-sm font-medium">{call.caller_name}</p>
        <StatusBadge status={call.status} />
      </div>
      <p className="truncate font-mono text-[10px] text-zinc-500">
        {call.mode === "status" ? "Step 4 · " : "Step 1 · "}{call.call_id}
      </p>
    </button>
  );
}

function Placeholder() {
  return (
    <div className="grid flex-1 place-items-center text-center text-zinc-500">
      <div>
        <PhoneCall className="mx-auto size-10 text-zinc-700" />
        <p className="mt-3 text-sm">Waiting for calls…</p>
        <p className="text-xs text-zinc-600">Press the green button on the customer phone</p>
      </div>
    </div>
  );
}

function AgentIndicator({ call }: { call: Call }) {
  if (call.status !== "active") return null;
  if (call.phase === "interview")
    return <span className="flex items-center gap-1.5 whitespace-nowrap rounded-full bg-sky-500/10 px-2.5 py-1 text-xs font-medium text-sky-300"><Headset className="size-3.5" /> Specialist on call</span>;
  if (call.phase === "handover")
    return <span className="flex items-center gap-1.5 whitespace-nowrap rounded-full bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-300"><Loader2 className="size-3.5 animate-spin" /> Waiting for specialist</span>;
  const map: Record<string, { icon: React.ReactNode; label: string; cls: string }> = {
    listening: { icon: <Ear className="size-3.5" />, label: "Listening", cls: "text-emerald-300 bg-emerald-500/10" },
    thinking: { icon: <Brain className="size-3.5 animate-pulse" />, label: "Thinking", cls: "text-amber-300 bg-amber-500/10" },
    speaking: { icon: <Volume2 className="size-3.5" />, label: "Speaking", cls: "text-indigo-300 bg-indigo-500/10" },
  };
  const m = map[call.agent_state ?? ""] ?? { icon: <Loader2 className="size-3.5 animate-spin" />, label: call.agent_state ?? "Joining", cls: "text-zinc-300 bg-zinc-800" };
  return (
    <div className="flex items-center gap-2">
      <span className={clsx("flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium", m.cls)}>{m.icon} AI · {m.label}</span>
      {call.user_state === "speaking" && (
        <span className="flex items-center gap-1.5 whitespace-nowrap rounded-full bg-sky-500/10 px-2.5 py-1 text-xs font-medium text-sky-300">
          <CircleDot className="size-3.5 animate-pulse" /> Customer
        </span>
      )}
    </div>
  );
}

export function Transcript({ call, warning, compact }: { call: Call; warning?: { message: string; at: number }; compact?: boolean }) {
  const now = useNow(500);
  const bottomRef = useRef<HTMLDivElement>(null);
  const start = call.answered_at ?? call.created_at;
  const end = call.ended_at ?? now;
  const isLive = ["connecting", "active"].includes(call.status);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [call.lines.length, call.live.customer, call.live.ai, call.live.specialist]);

  const liveBubbles = useMemo(
    // once the specialist is on the line the AI is silent: never show a (stale) "AI assistant" live bubble
    () => (["ai", "specialist", "customer"] as Speaker[])
      .filter((s) => call.live[s] && !(s === "ai" && call.phase === "interview"))
      .map((s) => ({ speaker: s, text: call.live[s]! })),
    [call.live.ai, call.live.customer, call.live.specialist, call.phase],
  );
  const handoverAt = call.lines.findIndex((l) => l.phase === "interview");

  return (
    <>
      {!compact && (
        <div className="flex items-center gap-3 border-b border-zinc-800 px-5 py-3">
          <div className="grid size-9 place-items-center rounded-full bg-zinc-800 text-sm font-semibold">
            {call.caller_name.split(" ").map((p) => p[0]).join("").slice(0, 2)}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <p className="truncate text-sm font-semibold">{call.caller_name}</p>
              <StatusBadge status={call.status} />
            </div>
            <p className="truncate font-mono text-xs text-zinc-500">{call.caller_number} · {call.call_id}</p>
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-3">
            <AgentIndicator call={call} />
            {call.answered_at && <span className="font-mono text-sm text-zinc-300">{fmtDuration(end - start)}</span>}
            {call.phase === "handover" && isLive && (
              <Link to="/interview" className="whitespace-nowrap rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold hover:bg-sky-500">
                Open Interview desk →
              </Link>
            )}
            {isLive && (
              <button onClick={() => api.hangup(call.call_id)}
                className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold hover:bg-rose-500">
                <PhoneOff className="size-3.5" /> End call
              </button>
            )}
          </div>
        </div>
      )}

      <AnimatePresence>
        {warning && now - warning.at < 6 && (
          <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="mx-5 mt-3 self-start rounded-full bg-amber-500/10 px-3 py-1 text-xs text-amber-300 ring-1 ring-amber-500/30">
            {warning.message}
          </motion.div>
        )}
      </AnimatePresence>
      {call.error && (
        <div className="mx-5 mt-3 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300 ring-1 ring-rose-500/30">⚠ {call.error}</div>
      )}

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
        {call.status === "connecting" && (
          <p className="flex items-center gap-2 text-xs text-amber-300"><Loader2 className="size-3.5 animate-spin" /> AI agent is picking up…</p>
        )}
        {call.lines.map((l, i) => (
          <div key={l.index}>
            {i === handoverAt && (
              <div className="my-3 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-sky-400">
                <span className="h-px flex-1 bg-sky-500/30" /> Warm handover · Step 2 interview <span className="h-px flex-1 bg-sky-500/30" />
              </div>
            )}
            <Bubble id={`line-${call.call_id}-${l.index}`} speaker={l.speaker} text={l.text}
              meta={`${fmtDuration(l.t)}${l.interrupted ? " · interrupted" : ""}`} index={l.index} />
          </div>
        ))}
        {liveBubbles.map((b) => <Bubble key={`live-${b.speaker}`} speaker={b.speaker} text={b.text} meta="live" live />)}
        {call.lines.length === 0 && liveBubbles.length === 0 && call.status === "active" && (
          <p className="text-xs text-zinc-500">Connected. Transcript will appear here…</p>
        )}
        <div ref={bottomRef} />
      </div>
    </>
  );
}

const SPEAKER = {
  ai: { label: "AI assistant", icon: <Bot className="size-4" />, avatar: "bg-indigo-500/20 text-indigo-300", bubble: "rounded-tl-sm bg-indigo-500/10 ring-indigo-500/20", right: false },
  specialist: { label: "Fraud specialist", icon: <UserRound className="size-4" />, avatar: "bg-sky-500/20 text-sky-300", bubble: "rounded-tl-sm bg-sky-500/10 ring-sky-500/20", right: false },
  customer: { label: "Customer", icon: <User className="size-4" />, avatar: "bg-zinc-700 text-zinc-200", bubble: "rounded-tr-sm bg-zinc-800 ring-zinc-700", right: true },
};

function Bubble({ speaker, text, meta, live, id, index }: {
  speaker: Speaker; text: string; meta: string; live?: boolean; id?: string; index?: number;
}) {
  const s = SPEAKER[speaker] ?? SPEAKER.customer;
  return (
    <motion.div id={id} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
      className={clsx("flex gap-2.5 rounded-xl", s.right && "flex-row-reverse")}>
      <div className={clsx("mt-0.5 grid size-7 shrink-0 place-items-center rounded-full", s.avatar)}>{s.icon}</div>
      <div className={clsx("max-w-[78%]", s.right && "text-right")}>
        <p className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">
          {s.label} · {meta}{index !== undefined && <span className="ml-1 text-zinc-600">#{index}</span>}
        </p>
        <div className={clsx("inline-block rounded-2xl px-3.5 py-2 text-left text-sm leading-relaxed text-zinc-100 ring-1", s.bubble, live && "caret opacity-80")}>
          {text}
        </div>
      </div>
    </motion.div>
  );
}
