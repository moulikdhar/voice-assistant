// "Agent Console": what the bank's support team sees. Incoming calls, live transcript, call record.
import { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import clsx from "clsx";
import {
  Bot,
  Brain,
  CircleDot,
  Ear,
  History,
  Loader2,
  PhoneCall,
  PhoneIncoming,
  PhoneOff,
  User,
  Volume2,
  Wifi,
  WifiOff,
} from "lucide-react";
import { api, fmtDuration, recordToCall, type Call, type HistoryItem } from "../lib/api";
import { useConsole, useNow } from "../lib/useConsole";
import SummaryPanel from "./SummaryPanel";

export default function Console() {
  const { calls, settings, warnings, connected } = useConsole();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [archived, setArchived] = useState<Call | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);

  // Auto-focus the newest ringing/active call
  const newestLive = calls.find((c) => ["ringing", "connecting", "active", "wrapping_up"].includes(c.status));
  useEffect(() => {
    if (newestLive) {
      setSelectedId(newestLive.call_id);
      setArchived(null);
    }
  }, [newestLive?.call_id]);

  // Load saved calls from disk (and refresh when a call is saved)
  const savedCount = calls.filter((c) => c.status === "ended").length;
  useEffect(() => {
    api.history().then(setHistory).catch(() => {});
  }, [savedCount]);

  const selected = archived ?? calls.find((c) => c.call_id === selectedId) ?? null;
  const ringing = calls.filter((c) => c.status === "ringing");
  const live = calls.filter((c) => ["connecting", "active", "wrapping_up"].includes(c.status));
  const sessionIds = new Set(calls.map((c) => c.call_id));

  async function openArchived(id: string) {
    const live = calls.find((c) => c.call_id === id);
    if (live) {
      setArchived(null);
      setSelectedId(id);
      return;
    }
    setArchived(recordToCall(await api.record(id)));
  }

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden rounded-2xl bg-zinc-900/60 ring-1 ring-zinc-800">
      {/* header */}
      <header className="flex items-center gap-3 border-b border-zinc-800 px-5 py-3">
        <div className="grid size-8 place-items-center rounded-lg bg-indigo-500/15 text-indigo-300">
          <PhoneCall className="size-4" />
        </div>
        <div>
          <h1 className="text-sm font-semibold">Fraud & Disputes Desk</h1>
          <p className="text-xs text-zinc-500">Agent Console · Voxtral Realtime · Mistral · Voxtral TTS</p>
        </div>
        <div className="ml-auto flex items-center gap-4 text-xs">
          <label className="flex cursor-pointer items-center gap-2 text-zinc-400">
            <span>Auto-answer with AI</span>
            <button
              onClick={() => api.settings({ auto_answer: !settings.auto_answer })}
              className={clsx("relative h-5 w-9 rounded-full transition",
                settings.auto_answer ? "bg-emerald-500" : "bg-zinc-700")}
            >
              <span className={clsx("absolute top-0.5 size-4 rounded-full bg-white transition-all",
                settings.auto_answer ? "left-[18px]" : "left-0.5")} />
            </button>
          </label>
          <span className={clsx("flex items-center gap-1.5", connected ? "text-emerald-400" : "text-rose-400")}>
            {connected ? <Wifi className="size-3.5" /> : <WifiOff className="size-3.5" />}
            {connected ? "Live" : "Reconnecting…"}
          </span>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* LEFT: call queue */}
        <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r border-zinc-800 p-4">
          <Section title="Incoming">
            <AnimatePresence>
              {ringing.map((c) => (
                <IncomingCard key={c.call_id} call={c} autoAnswer={settings.auto_answer} ringSeconds={settings.ring_seconds} />
              ))}
            </AnimatePresence>
            {ringing.length === 0 && <Empty text="No incoming calls" />}
          </Section>

          <Section title="On call">
            {live.map((c) => (
              <QueueRow key={c.call_id} call={c} active={selected?.call_id === c.call_id}
                onClick={() => { setArchived(null); setSelectedId(c.call_id); }} />
            ))}
            {live.length === 0 && <Empty text="No active calls" />}
          </Section>

          <Section title="Recent" icon={<History className="size-3.5" />}>
            {calls.filter((c) => ["ended", "missed", "declined"].includes(c.status)).map((c) => (
              <QueueRow key={c.call_id} call={c} active={selected?.call_id === c.call_id}
                onClick={() => { setArchived(null); setSelectedId(c.call_id); }} />
            ))}
            {history.filter((h) => !sessionIds.has(h.call_id)).map((h) => (
              <button key={h.call_id} onClick={() => openArchived(h.call_id)}
                className={clsx("w-full rounded-xl px-3 py-2 text-left text-xs ring-1 transition hover:bg-zinc-800/60",
                  selected?.call_id === h.call_id ? "bg-zinc-800 ring-zinc-600" : "ring-zinc-800")}>
                <p className="font-medium text-zinc-300">{h.caller?.name ?? "Unknown"}</p>
                <p className="truncate font-mono text-[10px] text-zinc-500">{h.call_id}</p>
              </button>
            ))}
          </Section>
        </aside>

        {/* CENTER: transcript */}
        <main className="flex min-w-0 flex-1 flex-col">
          {selected ? <Transcript call={selected} warning={warnings[selected.call_id]} /> : <Placeholder />}
        </main>

        {/* RIGHT: call record + summary */}
        <aside className="w-[360px] shrink-0 overflow-y-auto border-l border-zinc-800 p-4">
          {selected ? <SummaryPanel call={selected} /> : null}
        </aside>
      </div>
    </div>
  );
}

function Section({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
        {icon}
        {title}
      </h2>
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
      <div className="mt-3 flex gap-2">
        <button onClick={() => api.answer(call.call_id)}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-emerald-500 py-1.5 text-xs font-semibold text-black hover:bg-emerald-400">
          <Bot className="size-3.5" /> Answer with AI
        </button>
        <button onClick={() => api.decline(call.call_id)}
          className="rounded-lg bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:bg-rose-600 hover:text-white">
          Decline
        </button>
      </div>
      {autoAnswer && (
        <div className="absolute bottom-0 left-0 h-0.5 bg-emerald-400 transition-all"
          style={{ width: `${Math.min(100, (ringFor / ringSeconds) * 100)}%` }} />
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

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide", STATUS_STYLE[status])}>
      {status.replace("_", " ")}
    </span>
  );
}

function QueueRow({ call, active, onClick }: { call: Call; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className={clsx("w-full rounded-xl px-3 py-2 text-left ring-1 transition hover:bg-zinc-800/60",
        active ? "bg-zinc-800 ring-zinc-600" : "ring-zinc-800")}>
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-sm font-medium">{call.caller_name}</p>
        <StatusBadge status={call.status} />
      </div>
      <p className="truncate font-mono text-[10px] text-zinc-500">{call.call_id}</p>
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
  const s = call.agent_state;
  const map: Record<string, { icon: React.ReactNode; label: string; cls: string }> = {
    listening: { icon: <Ear className="size-3.5" />, label: "Listening", cls: "text-emerald-300 bg-emerald-500/10" },
    thinking: { icon: <Brain className="size-3.5 animate-pulse" />, label: "Thinking", cls: "text-amber-300 bg-amber-500/10" },
    speaking: { icon: <Volume2 className="size-3.5" />, label: "Speaking", cls: "text-indigo-300 bg-indigo-500/10" },
  };
  const m = map[s ?? ""] ?? { icon: <Loader2 className="size-3.5 animate-spin" />, label: s ?? "Joining", cls: "text-zinc-300 bg-zinc-800" };
  return (
    <div className="flex items-center gap-2">
      <span className={clsx("flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium", m.cls)}>
        {m.icon} AI · {m.label}
      </span>
      {call.user_state === "speaking" && (
        <span className="flex items-center gap-1.5 whitespace-nowrap rounded-full bg-sky-500/10 px-2.5 py-1 text-xs font-medium text-sky-300">
          <CircleDot className="size-3.5 animate-pulse" /> Customer speaking
        </span>
      )}
    </div>
  );
}

function Transcript({ call, warning }: { call: Call; warning?: { message: string; at: number } }) {
  const now = useNow(500);
  const bottomRef = useRef<HTMLDivElement>(null);
  const start = call.answered_at ?? call.created_at;
  const end = call.ended_at ?? now;
  const isLive = ["connecting", "active"].includes(call.status);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [call.lines.length, call.live.customer, call.live.ai]);

  const liveBubbles = useMemo(() => {
    const out: { speaker: "customer" | "ai"; text: string }[] = [];
    if (call.live.ai) out.push({ speaker: "ai", text: call.live.ai });
    if (call.live.customer) out.push({ speaker: "customer", text: call.live.customer });
    return out;
  }, [call.live.ai, call.live.customer]);

  return (
    <>
      <div className="flex items-center gap-3 border-b border-zinc-800 px-5 py-3">
        <div className="grid size-9 place-items-center rounded-full bg-zinc-800 text-sm font-semibold">
          {call.caller_name.split(" ").map((p) => p[0]).join("").slice(0, 2)}
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-semibold">{call.caller_name}</p>
            <StatusBadge status={call.status} />
          </div>
          <p className="truncate font-mono text-xs text-zinc-500">
            {call.caller_number} · {call.call_id}
          </p>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-3">
          <AgentIndicator call={call} />
          {call.answered_at && <span className="font-mono text-sm text-zinc-300">{fmtDuration(end - start)}</span>}
          {isLive && (
            <button onClick={() => api.hangup(call.call_id)}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold hover:bg-rose-500">
              <PhoneOff className="size-3.5" /> End call
            </button>
          )}
        </div>
      </div>

      <AnimatePresence>
        {warning && now - warning.at < 6 && (
          <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="mx-5 mt-3 self-start rounded-full bg-amber-500/10 px-3 py-1 text-xs text-amber-300 ring-1 ring-amber-500/30">
            {warning.message}
          </motion.div>
        )}
      </AnimatePresence>

      {call.error && (
        <div className="mx-5 mt-3 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300 ring-1 ring-rose-500/30">
          ⚠ {call.error}
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
        {call.status === "connecting" && (
          <p className="flex items-center gap-2 text-xs text-amber-300"><Loader2 className="size-3.5 animate-spin" /> AI agent is picking up…</p>
        )}
        {call.lines.map((l) => (
          <Bubble key={l.index} id={`line-${call.call_id}-${l.index}`} speaker={l.speaker} text={l.text}
            meta={`${fmtDuration(l.t)}${l.interrupted ? " · interrupted" : ""}`} index={l.index} />
        ))}
        {liveBubbles.map((b) => (
          <Bubble key={`live-${b.speaker}`} speaker={b.speaker} text={b.text} meta="live" live />
        ))}
        {call.lines.length === 0 && liveBubbles.length === 0 && call.status === "active" && (
          <p className="text-xs text-zinc-500">Connected. Transcript will appear here…</p>
        )}
        <div ref={bottomRef} />
      </div>
    </>
  );
}

function Bubble({ speaker, text, meta, live, id, index }: {
  speaker: "customer" | "ai"; text: string; meta: string; live?: boolean; id?: string; index?: number;
}) {
  const ai = speaker === "ai";
  return (
    <motion.div id={id} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
      className={clsx("flex gap-2.5 rounded-xl", ai ? "" : "flex-row-reverse")}>
      <div className={clsx("mt-0.5 grid size-7 shrink-0 place-items-center rounded-full",
        ai ? "bg-indigo-500/20 text-indigo-300" : "bg-sky-500/20 text-sky-300")}>
        {ai ? <Bot className="size-4" /> : <User className="size-4" />}
      </div>
      <div className={clsx("max-w-[75%]", ai ? "" : "text-right")}>
        <p className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">
          {ai ? "AI assistant" : "Customer"} · {meta}
          {index !== undefined && <span className="ml-1 text-zinc-600">#{index}</span>}
        </p>
        <div className={clsx("inline-block rounded-2xl px-3.5 py-2 text-left text-sm leading-relaxed",
          ai ? "rounded-tl-sm bg-indigo-500/10 text-zinc-100 ring-1 ring-indigo-500/20"
             : "rounded-tr-sm bg-zinc-800 text-zinc-100 ring-1 ring-zinc-700",
          live && "caret opacity-80")}>
          {text}
        </div>
      </div>
    </motion.div>
  );
}

