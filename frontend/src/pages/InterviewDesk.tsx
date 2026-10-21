// STEP 2 · Live Interview Assist: the human fraud specialist's desk.
import { useEffect, useState } from "react";
import { Room } from "livekit-client";
import { RoomAudioRenderer, RoomContext, StartAudio } from "@livekit/components-react";
import clsx from "clsx";
import {
  AlertTriangle, BookOpen, Bot, CheckCircle2, CircleDashed, CircleDot, Headset, Lightbulb, Loader2, Mic, MicOff, PhoneOff, Quote, ScrollText, Sparkles,
} from "lucide-react";
import { api, fmtDuration, money, type Call } from "../lib/api";
import { useLive, useNow } from "../lib/live";
import { Transcript } from "../components/Console";
import { Card } from "../components/LiveClaimPanel";

export default function InterviewDesk() {
  const { callList, claims, warnings } = useLive();
  const queue = callList.filter((c) => c.status === "active" && (c.phase === "handover" || c.phase === "interview"));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [joinedId, setJoinedId] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [joining, setJoining] = useState(false);
  const [simStarting, setSimStarting] = useState(false);

  // keep the selection on a live call (after a reset or a new handover, jump to the waiting call)
  const queueKey = queue.map((c) => c.call_id).join();
  useEffect(() => {
    if (joinedId) return;
    if (queue[0] && !queue.some((c) => c.call_id === selectedId)) setSelectedId(queue[0].call_id);
  }, [queueKey]);

  const call = callList.find((c) => c.call_id === (joinedId ?? selectedId)) ?? null;
  const claim = call?.claim_id ? claims[call.claim_id] : undefined;
  const summary = claim?.intake_summary ?? call?.summary;
  const simulated = call?.specialist?.mode === "simulated";

  async function simulate(c: Call) {
    setSimStarting(true);
    try { await api.simulateSpecialist(c.call_id); }
    catch (e) { alert(`Could not start the simulated specialist: ${e instanceof Error ? e.message : e}`); }
    finally { setSimStarting(false); }
  }

  // leave the room when the call ends
  useEffect(() => {
    if (joinedId && call && call.status !== "active") leave();
  }, [call?.status]);
  useEffect(() => () => void room?.disconnect(), [room]);

  async function join(c: Call) {
    setJoining(true);
    let r: Room | null = null;
    try {
      const { livekit_url, token } = await api.specialistJoin(c.call_id);
      r = new Room();
      await r.connect(livekit_url, token);
      await r.localParticipant.setMicrophoneEnabled(true, { echoCancellation: true, noiseSuppression: true });
      setRoom(r);
      setJoinedId(c.call_id);
      setMuted(false);
    } catch (e) {
      void r?.disconnect();
      alert(`Could not join the call: ${e instanceof Error ? e.message : e}`);
    } finally {
      setJoining(false);
    }
  }
  function leave() {
    void room?.disconnect();
    setRoom(null);
    setJoinedId(null);
  }
  async function toggleMute() {
    if (!room) return;
    await room.localParticipant.setMicrophoneEnabled(muted);
    setMuted(!muted);
  }

  return (
    <div className="flex h-full gap-4 p-4">
      {/* queue */}
      <aside className="flex w-60 shrink-0 flex-col gap-3 overflow-y-auto rounded-2xl bg-zinc-900/60 p-4 ring-1 ring-zinc-800">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><Headset className="size-4 text-sky-300" /> Specialist desk</h2>
        <p className="text-xs text-zinc-500">Calls the AI has handed over. The customer is still on the line.</p>
        {queue.map((c) => <QueueCard key={c.call_id} call={c} active={c.call_id === call?.call_id} onClick={() => setSelectedId(c.call_id)} />)}
        {queue.length === 0 && (
          <p className="rounded-xl border border-dashed border-zinc-800 p-3 text-center text-xs text-zinc-600">
            No live handover right now.
          </p>
        )}
        <div className="mt-auto rounded-lg bg-zinc-800/50 p-2.5 text-[11px] leading-relaxed text-zinc-400">
          Alone? Click <b>Simulate specialist</b>: an AI plays the specialist with a second voice and you just answer as the customer.
          Playing both roles yourself? Mute the phone while you speak as the specialist, and the other way round.
        </div>
      </aside>

      {/* call + transcript */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl bg-zinc-900/60 ring-1 ring-zinc-800">
        {!call ? (
          <div className="grid flex-1 place-items-center p-8 text-center">
            <div className="max-w-sm">
              <Headset className="mx-auto size-8 text-zinc-600" />
              <p className="mt-3 text-sm font-medium text-zinc-300">No call is waiting for a specialist</p>
              <p className="mt-1 text-xs leading-relaxed text-zinc-500">
                Nothing is loading here. A call appears on this desk only while it is live and Asha has transferred it.
                Start a call on the phone: after the claim (or when the caller insists on a human) Asha transfers it,
                and this screen opens by itself.
              </p>
            </div>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-3 border-b border-zinc-800 px-5 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{call.caller_name} <span className="font-mono text-xs text-zinc-500">· {claim?.reference ?? "no claim yet"}</span></p>
                <p className="text-xs text-zinc-500">
                  {simulated ? `Simulated specialist ${call.specialist?.name} is on the call (Mistral Large + Voxtral TTS) · ${call.specialist?.status}`
                    : joinedId ? "You are on the call · both channels transcribed live by Voxtral Realtime" : "Customer waiting on the line"}
                </p>
              </div>
              <div className="ml-auto flex items-center gap-2">
                {room && (
                  <RoomContext.Provider value={room}>
                    <RoomAudioRenderer />
                    <StartAudio label="Enable audio" className="rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-semibold text-black" />
                  </RoomContext.Provider>
                )}
                {simulated ? (
                  <>
                    <span className="flex items-center gap-1.5 rounded-lg bg-violet-500/15 px-3 py-1.5 text-xs font-semibold text-violet-200">
                      <Bot className="size-3.5" /> {call.specialist?.name} · {call.specialist?.status}
                    </span>
                    {call.status === "active" && (
                      <button onClick={() => void api.endInterview(call.call_id).catch(() => {})}
                        className="flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold hover:bg-rose-500">
                        <PhoneOff className="size-3.5" /> End interview
                      </button>
                    )}
                  </>
                ) : !joinedId ? (
                  <>
                    <button disabled={simStarting || call.status !== "active"} onClick={() => simulate(call)}
                      title="An AI plays the human specialist, so you only need to play the customer"
                      className="flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold hover:bg-violet-500 disabled:opacity-40">
                      {simStarting ? <Loader2 className="size-3.5 animate-spin" /> : <Bot className="size-3.5" />} Simulate specialist
                    </button>
                    <button disabled={joining || call.status !== "active"} onClick={() => join(call)}
                      className="flex items-center gap-1.5 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold hover:bg-sky-500 disabled:opacity-40">
                      {joining ? <Loader2 className="size-3.5 animate-spin" /> : <Headset className="size-3.5" />} Join call
                    </button>
                  </>
                ) : (
                  <>
                    <button onClick={toggleMute}
                      className={clsx("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold", muted ? "bg-white text-black" : "bg-zinc-800 hover:bg-zinc-700")}>
                      {muted ? <MicOff className="size-3.5" /> : <Mic className="size-3.5" />} {muted ? "Muted" : "Mic on"}
                    </button>
                    <button onClick={() => void api.endInterview(call.call_id).catch(() => {})}
                      className="flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold hover:bg-rose-500">
                      <PhoneOff className="size-3.5" /> End interview
                    </button>
                  </>
                )}
              </div>
            </div>

            {summary?.short_summary && (
              <div className="mx-5 mt-4 rounded-xl bg-indigo-500/10 p-3 ring-1 ring-indigo-500/25">
                <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-indigo-300">
                  <Sparkles className="size-3.5" /> Warm handover · AI intake summary
                </p>
                <p className="text-sm leading-relaxed text-zinc-200">{summary.short_summary}</p>
                {claim && (
                  <p className="mt-1.5 text-xs text-zinc-400">
                    {claim.disputed_transactions.map((t) => `${t.merchant} ${money(t.amount, t.currency)}`).join(" · ")}
                  </p>
                )}
              </div>
            )}
            {!summary && call.phase === "handover" && (
              <p className="mx-5 mt-4 flex items-center gap-2 text-xs text-indigo-300"><Loader2 className="size-3.5 animate-spin" /> Mistral is writing the intake summary…</p>
            )}
            <Transcript call={call} warning={warnings[call.call_id]} compact />
          </>
        )}
      </main>

      {/* checklist */}
      <aside className="w-[380px] shrink-0 space-y-3 overflow-y-auto">
        {call ? (call.mode === "status" ? <CallbackPanel call={call} /> : <AssistPanel call={call} />) : null}
      </aside>
    </div>
  );
}

function QueueCard({ call, active, onClick }: { call: Call; active: boolean; onClick: () => void }) {
  const now = useNow(1000);
  return (
    <button onClick={onClick}
      className={clsx("w-full rounded-xl p-3 text-left ring-1 transition", active ? "bg-sky-500/10 ring-sky-500/40" : "ring-zinc-800 hover:bg-zinc-800/60")}>
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold">{call.caller_name}</p>
        <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase",
          call.phase === "interview" ? "bg-sky-500/15 text-sky-300" : "bg-amber-500/15 text-amber-300")}>
          {call.phase === "interview" ? "in interview" : "waiting"}
        </span>
      </div>
      <p className="font-mono text-[10px] text-zinc-500">{call.call_id} · {fmtDuration(now - (call.answered_at ?? call.created_at))}</p>
    </button>
  );
}

function AssistPanel({ call }: { call: Call }) {
  const a = call.assist;
  if (!a)
    return (
      <Card title="Liability checklist">
        <p className="flex items-center gap-2 text-xs text-zinc-500"><Loader2 className="size-3.5 animate-spin" /> Mistral Large is preparing the checklist from the intake call…</p>
      </Card>
    );
  const done = (a.checklist ?? []).filter((c) => c.status === "answered").length;
  return (
    <>
      {a.next_question && (
        <div className="rounded-xl bg-sky-500/10 p-3.5 ring-1 ring-sky-500/30">
          <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-sky-300"><Lightbulb className="size-3.5" /> Ask next</p>
          <p className="text-sm text-zinc-100">{a.next_question}</p>
        </div>
      )}
      {!!a.alerts?.length && (
        <div className="space-y-1.5 rounded-xl bg-rose-500/10 p-3 ring-1 ring-rose-500/30">
          {a.alerts.map((t, i) => (
            <p key={i} className="flex gap-2 text-xs text-rose-200"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> {t}</p>
          ))}
          <p className="text-[10px] text-rose-300/60">Prompts for the specialist, not findings.</p>
        </div>
      )}
      <Card title={`Liability checklist · ${done}/${a.checklist?.length ?? 0}`} icon={<ScrollText className="size-3.5" />}>
        <ul className="space-y-2">
          {(a.checklist ?? []).map((c) => (
            <li key={c.id} className="rounded-lg bg-zinc-800/40 p-2">
              <p className="flex items-start gap-2 text-xs text-zinc-200">
                {c.status === "answered" ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-400" />
                  : c.status === "partial" ? <CircleDot className="mt-0.5 size-3.5 shrink-0 text-amber-400" />
                  : <CircleDashed className="mt-0.5 size-3.5 shrink-0 text-zinc-500" />}
                {c.question}
              </p>
              {c.answer && <p className="ml-5 mt-1 text-xs text-zinc-300">{c.answer}{c.source && <span className="ml-1 text-[10px] text-zinc-500">({c.source})</span>}</p>}
              {c.evidence && <p className="ml-5 mt-0.5 flex gap-1 text-[11px] text-zinc-500"><Quote className="mt-0.5 size-3 shrink-0" />{c.evidence}</p>}
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Required disclosures">
        <ul className="space-y-1.5">
          {(a.disclosures ?? []).map((d) => (
            <li key={d.id} className="flex items-start gap-2 text-xs">
              {d.done ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-400" /> : <CircleDashed className="mt-0.5 size-3.5 shrink-0 text-amber-400" />}
              <span className={d.done ? "text-zinc-400" : "text-zinc-100"}>{d.text}</span>
            </li>
          ))}
        </ul>
      </Card>
      {a.policy_hint && <p className="flex gap-2 rounded-xl bg-zinc-900 p-3 text-xs text-zinc-400 ring-1 ring-zinc-800"><BookOpen className="mt-0.5 size-3.5 shrink-0" /> {a.policy_hint}</p>}
      {a.updated_at && <p className="text-center text-[10px] text-zinc-600">updated {new Date(a.updated_at).toLocaleTimeString()} · Mistral Large</p>}
    </>
  );
}

/** Call-back escalation (Step 4): no liability checklist; show why the AI transferred the caller. */
function CallbackPanel({ call }: { call: Call }) {
  const { claims } = useLive();
  const claim = call.claim_id ? claims[call.claim_id] : undefined;
  const outcome = claim?.refund ? `Refunded ${money(claim.refund.amount)}` : claim?.decision?.outcome?.replace("_", " ") ?? claim?.status;
  return (
    <>
      <div className="rounded-xl bg-amber-500/10 p-3.5 ring-1 ring-amber-500/30">
        <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-amber-300"><AlertTriangle className="size-3.5" /> Call-back escalation</p>
        <p className="text-sm text-zinc-100">{call.handover_reason ?? "The caller asked for a specialist."}</p>
      </div>
      <Card title="Claim on file">
        <p className="font-mono text-sm">{claim?.reference ?? "—"}</p>
        <p className="mt-1 text-xs text-zinc-300">Current outcome: <span className="font-semibold">{outcome ?? "—"}</span></p>
        {claim?.decision?.notes && <p className="mt-1 text-xs text-zinc-400">Analyst's reason: {claim.decision.notes}</p>}
      </Card>
    </>
  );
}
