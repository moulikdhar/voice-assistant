// STEP 3 · Post-Call Brief + decision (and the Step 4 follow-up log) for the fraud analyst.
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import clsx from "clsx";
import {
  AlertTriangle, BadgeCheck, CheckCircle2, Clock3, FileAudio, Gavel, Loader2, Quote, RefreshCw, ShieldAlert, XCircle,
} from "lucide-react";
import { api, fmtDuration, fmtTime, money, type Claim } from "../lib/api";
import { useLive } from "../lib/live";
import { Card } from "../components/LiveClaimPanel";

const TABS = ["Brief", "QA scorecard", "Calls & transcripts", "Audit log", "Success measures"] as const;

export default function Investigator() {
  const { claimList, claims, briefProgress } = useLive();
  const [params, setParams] = useSearchParams();
  const fromUrl = params.get("claim");
  const selectedId = (fromUrl && claims[fromUrl] ? fromUrl : null) ?? claimList[0]?.id ?? null;
  const [detail, setDetail] = useState<Claim | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Brief");
  const live = selectedId ? claims[selectedId] : undefined;

  // refetch the full view whenever the live claim changes
  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    let alive = true; // ignore answers that arrive after a newer request (or for another claim)
    api.claim(selectedId).then((d) => alive && setDetail(d)).catch(() => alive && setDetail(null));
    return () => { alive = false; };
  }, [selectedId, live?.updated_at, live?.brief?.generated_at]);

  const c = detail?.id === selectedId ? detail : null;
  const progress = selectedId ? briefProgress[selectedId] : undefined;

  return (
    <div className="flex h-full gap-4 p-4">
      <aside className="w-72 shrink-0 space-y-2 overflow-y-auto rounded-2xl bg-zinc-900/60 p-4 ring-1 ring-zinc-800">
        <h2 className="text-sm font-semibold">Claims</h2>
        {claimList.map((cl) => (
          <button key={cl.id} onClick={() => setParams({ claim: cl.id })}
            className={clsx("w-full rounded-xl p-3 text-left ring-1 transition", cl.id === selectedId ? "bg-zinc-800 ring-zinc-600" : "ring-zinc-800 hover:bg-zinc-800/60")}>
            <div className="flex items-center justify-between">
              <p className="font-mono text-xs font-semibold">{cl.reference}</p>
              <StatusChip status={cl.status} />
            </div>
            <p className="text-sm">{cl.customer_name}</p>
            <p className="text-[11px] text-zinc-500">{money(cl.total_disputed, cl.currency)} · {cl.clock?.business_days_left ?? "—"} business days left</p>
          </button>
        ))}
        {claimList.length === 0 && <p className="rounded-xl border border-dashed border-zinc-800 p-3 text-center text-xs text-zinc-600">No claims yet. Run Step 1 first.</p>}
      </aside>

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl bg-zinc-900/60 ring-1 ring-zinc-800">
        {!c ? (
          <div className="grid flex-1 place-items-center text-sm text-zinc-500">Select a claim</div>
        ) : (
          <>
            <ClaimHeader c={c} />
            <div className="flex items-center gap-1 border-b border-zinc-800 px-4">
              {TABS.map((t) => (
                <button key={t} onClick={() => setTab(t)}
                  className={clsx("border-b-2 px-3 py-2.5 text-xs font-medium", tab === t ? "border-indigo-400 text-zinc-100" : "border-transparent text-zinc-500 hover:text-zinc-300")}>
                  {t}
                </button>
              ))}
              <button onClick={() => api.brief(c.id)} disabled={!!progress && progress !== "done"}
                className="ml-auto flex items-center gap-1.5 rounded-lg bg-zinc-800 px-2.5 py-1.5 text-xs hover:bg-zinc-700 disabled:opacity-50">
                {progress && progress !== "done" ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
                {progress === "transcribing" ? "Voxtral Transcribe V2…" : progress === "analysing" ? "Mistral Large…" : "Regenerate brief"}
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              {tab === "Brief" && <BriefTab c={c} progress={progress} />}
              {tab === "QA scorecard" && <QaTab c={c} />}
              {tab === "Calls & transcripts" && <CallsTab c={c} />}
              {tab === "Audit log" && <AuditTab c={c} />}
              {tab === "Success measures" && <MeasuresTab c={c} />}
            </div>
          </>
        )}
      </main>

      {c && (
        <aside className="w-80 shrink-0 space-y-3 overflow-y-auto">
          <DecisionPanel key={c.id} c={c} onDone={setDetail} />
          <FollowupPanel c={c} />
        </aside>
      )}
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const cls: Record<string, string> = {
    open: "bg-zinc-700 text-zinc-200", interview: "bg-sky-500/15 text-sky-300",
    investigation: "bg-amber-500/15 text-amber-300", decided: "bg-emerald-500/15 text-emerald-300",
    refunded: "bg-emerald-500/25 text-emerald-200",
  };
  return <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase", cls[status])}>{status}</span>;
}

const STEPS: { key: keyof Claim["steps"]; label: string }[] = [
  { key: "report", label: "1 Report" }, { key: "interview", label: "2 Interview" },
  { key: "investigate", label: "3 Investigate" }, { key: "followup", label: "4 Follow-up" },
];

function ClaimHeader({ c }: { c: Claim }) {
  const days = c.clock?.business_days_left;
  return (
    <div className="flex flex-wrap items-center gap-5 border-b border-zinc-800 px-5 py-4">
      <div>
        <p className="font-mono text-lg font-semibold">{c.reference}</p>
        <p className="text-xs text-zinc-400">{c.customer_name} · {c.jurisdiction.replace("_", " ")} · opened {fmtTime(c.opened_at)}</p>
      </div>
      <div className={clsx("rounded-xl px-4 py-2 ring-1", c.clock?.breached ? "bg-rose-500/10 ring-rose-500/40" : days != null && days <= 3 ? "bg-amber-500/10 ring-amber-500/40" : "bg-zinc-800/60 ring-zinc-700")}>
        <p className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-zinc-400"><Clock3 className="size-3.5" /> Regulatory clock</p>
        <p className="text-sm"><span className="font-mono text-xl font-semibold">{days ?? "—"}</span> business days left · due {fmtTime(c.deadline_at)}</p>
        <p className="max-w-xs text-[10px] text-zinc-500">{c.clock?.rule} Starts at the customer's first call.</p>
      </div>
      <div>
        <p className="text-[10px] uppercase tracking-wider text-zinc-500">Disputed</p>
        <p className="font-mono text-lg">{money(c.total_disputed, c.currency)}</p>
      </div>
      <div className="ml-auto flex items-center gap-1">
        {STEPS.map((s, i) => {
          const v = c.steps?.[s.key] ?? "pending";
          const done = ["done", "decided", "brief_ready"].includes(v);
          const now = ["in_progress", "waiting"].includes(v);
          return (
            <div key={s.key} className="flex items-center gap-1">
              <span className={clsx("rounded-full px-2.5 py-1 text-[11px] font-medium",
                done ? "bg-emerald-500/15 text-emerald-300" : now ? "bg-sky-500/15 text-sky-300" : "bg-zinc-800 text-zinc-500")}>
                {s.label}
              </span>
              {i < STEPS.length - 1 && <span className="text-zinc-700">›</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function BriefTab({ c, progress }: { c: Claim; progress?: string }) {
  const b = c.brief;
  if (!b)
    return (
      <p className="flex items-center gap-2 text-sm text-zinc-400">
        {progress && progress !== "done" ? <Loader2 className="size-4 animate-spin" /> : null}
        {progress && progress !== "done" ? "Generating the brief…" : "The brief is generated automatically when the interview call ends. You can also click Regenerate brief."}
      </p>
    );
  if (b.error) return <p className="rounded-lg bg-rose-500/10 p-3 text-sm text-rose-300">{b.error}</p>;
  return (
    <div className="space-y-4">
      <Card title="Decision-ready summary"><p className="text-sm leading-relaxed text-zinc-200">{b.decision_ready_summary}</p></Card>
      {!!b.conflicts?.length && (
        <Card title={`Conflicting statements · ${b.conflicts.length}`} icon={<ShieldAlert className="size-3.5 text-rose-400" />}>
          <div className="space-y-2.5">
            {b.conflicts.map((x, i) => (
              <div key={i} className={clsx("rounded-lg p-3 ring-1", x.severity === "high" ? "bg-rose-500/10 ring-rose-500/30" : "bg-amber-500/10 ring-amber-500/30")}>
                <p className="text-sm font-semibold text-zinc-100">{x.title} <span className="ml-1 text-[10px] uppercase text-zinc-400">{x.severity}</span></p>
                <p className="mt-0.5 text-xs text-zinc-300">{x.detail}</p>
                {x.statements?.map((s, j) => (
                  <p key={j} className="mt-1 flex gap-1.5 text-[11px] text-zinc-400"><Quote className="mt-0.5 size-3 shrink-0" /><span className="text-zinc-500">{s.source}:</span> “{s.quote}”</p>
                ))}
              </div>
            ))}
            <p className="text-[10px] text-zinc-500">Flags are prompts for the analyst, not findings.</p>
          </div>
        </Card>
      )}
      <div className="grid grid-cols-2 gap-4">
        <Card title="Timeline">
          <ol className="relative space-y-3 border-l border-zinc-700 pl-4">
            {(b.timeline ?? []).map((t, i) => (
              <li key={i} className="text-xs">
                <span className="absolute -left-[5px] mt-1 size-2.5 rounded-full bg-indigo-400" />
                <p className="font-mono text-[10px] text-zinc-500">{t.time} · {t.source}</p>
                <p className="text-zinc-200">{t.event}</p>
              </li>
            ))}
          </ol>
        </Card>
        <Card title="Key facts with evidence">
          <ul className="space-y-2">
            {(b.key_facts ?? []).map((f, i) => (
              <li key={i} className="rounded-lg bg-zinc-800/50 p-2 text-xs">
                <p className="flex justify-between text-zinc-200"><span>{f.fact}</span><span className="font-semibold">{f.value}</span></p>
                {f.evidence?.quote && <p className="mt-1 flex gap-1 text-[11px] text-zinc-500"><Quote className="mt-0.5 size-3 shrink-0" />“{f.evidence.quote}”</p>}
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <ListCard title="Open questions" items={b.open_questions} />
        <ListCard title="Recommended next steps" items={b.recommended_next_steps} />
      </div>
      <p className="text-right text-[10px] text-zinc-600">generated {fmtTime(b.generated_at)} · Voxtral Mini Transcribe V2 + Mistral Large</p>
    </div>
  );
}

function ListCard({ title, items }: { title: string; items?: string[] }) {
  return (
    <Card title={title}>
      {items?.length ? <ul className="space-y-1">{items.map((x, i) => <li key={i} className="text-xs text-zinc-300">• {x}</li>)}</ul>
        : <p className="text-xs text-zinc-500">None</p>}
    </Card>
  );
}

function QaTab({ c }: { c: Claim }) {
  const rows = c.brief?.qa_scorecard ?? [];
  const scores = c.brief?.qa_scores ?? {};
  if (!rows.length) return <p className="text-sm text-zinc-500">QA scorecard appears with the brief.</p>;
  const byCall = rows.reduce<Record<string, typeof rows>>((a, r) => ((a[r.call_id] ??= []).push(r), a), {});
  return (
    <div className="space-y-4">
      {Object.entries(byCall).map(([callId, list]) => (
        <Card key={callId} title={`Call ${callId}`}>
          <p className="mb-2 text-sm">QA score <span className="font-mono text-lg font-semibold">{scores[callId]?.score_pct ?? "—"}%</span>
            <span className="ml-2 text-xs text-zinc-500">({scores[callId]?.met ?? 0} met · {scores[callId]?.missed ?? 0} missed)</span></p>
          <table className="w-full text-xs">
            <tbody>
              {(list ?? []).map((r, i) => (
                <tr key={i} className="border-t border-zinc-800">
                  <td className="py-1.5 pr-2">{r.met === true ? <CheckCircle2 className="size-4 text-emerald-400" /> : r.met === false ? <XCircle className="size-4 text-rose-400" /> : <span className="text-zinc-600">n/a</span>}</td>
                  <td className="py-1.5 pr-2 text-zinc-200">{r.criterion}</td>
                  <td className="py-1.5 text-zinc-500">{r.evidence ? `“${r.evidence}”` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}
      <p className="text-[10px] text-zinc-500">100% of calls are scored (today QA samples under 5%). Agreement with human reviewers is a POC success measure.</p>
    </div>
  );
}

function CallsTab({ c }: { c: Claim }) {
  return (
    <div className="space-y-4">
      {(c.call_details ?? []).map((call) => {
        const segs = call.batch_transcript?.segments ?? [];
        return (
          <Card key={call.call_id} title={`${call.mode === "status" ? "Step 4 follow-up" : "Step 1–2 report + interview"} · ${call.call_id}`} icon={<FileAudio className="size-3.5" />}>
            <p className="mb-2 text-xs text-zinc-400">
              {fmtTime(new Date(call.created_at * 1000).toISOString())} · {call.answered_at && call.ended_at ? fmtDuration(call.ended_at - call.answered_at) : "—"} · {call.lines.length} live lines
            </p>
            {call.audio_file && <audio controls src={`/api/calls/${call.call_id}/audio`} className="mb-3 h-8 w-full" />}
            {segs.length > 0 ? (
              <>
                <p className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">Voxtral Transcribe V2 · speaker diarisation</p>
                <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg bg-zinc-950/60 p-2">
                  {segs.map((s, i) => (
                    <p key={i} className="text-xs"><span className="font-mono text-[10px] text-zinc-500">{s.start?.toFixed?.(1)}s</span>{" "}
                      <span className={clsx("font-semibold", ["text-indigo-300", "text-zinc-200", "text-sky-300"][Number(s.speaker ?? 0) % 3])}>Speaker {s.speaker ?? "?"}</span>: <span className="text-zinc-300">{s.text}</span></p>
                  ))}
                </div>
              </>
            ) : (
              <>
                <p className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">
                  Live transcript (Voxtral Realtime){call.batch_transcript?.error ? ` · batch transcription failed: ${call.batch_transcript.error}` : ""}
                </p>
                <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg bg-zinc-950/60 p-2">
                  {call.lines.map((l) => (
                    <p key={l.index} className="text-xs"><span className={clsx("font-semibold", l.speaker === "ai" ? "text-indigo-300" : l.speaker === "specialist" ? "text-sky-300" : "text-zinc-200")}>{l.speaker}</span>: <span className="text-zinc-300">{l.text}</span></p>
                  ))}
                </div>
              </>
            )}
          </Card>
        );
      })}
    </div>
  );
}

function AuditTab({ c }: { c: Claim }) {
  return (
    <table className="w-full text-xs">
      <thead><tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500"><th className="py-2">Time</th><th>Actor</th><th>Action</th><th>Detail</th></tr></thead>
      <tbody>
        {(c.audit ?? []).map((a) => (
          <tr key={a.id} className="border-t border-zinc-800">
            <td className="py-1.5 pr-3 font-mono text-[10px] text-zinc-500">{fmtTime(a.time)}</td>
            <td className="pr-3 text-zinc-400">{a.actor}</td>
            <td className="pr-3 font-mono text-[11px] text-zinc-300">{a.action}</td>
            <td className="text-zinc-300">{a.detail}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MeasuresTab({ c }: { c: Claim }) {
  const m = c.metrics;
  const repeated = c.brief?.repeated_questions ?? [];
  const complete = c.disputed_transactions.length > 0 && !!c.facts && c.steps.interview === "done";
  return (
    <div className="grid grid-cols-2 gap-4">
      <Card title="No fact re-asked across steps">
        {c.brief ? (repeated.length === 0
          ? <p className="flex items-center gap-2 text-sm text-emerald-300"><BadgeCheck className="size-4" /> Nothing had to be asked twice</p>
          : <ul className="space-y-1">{repeated.map((r, i) => <li key={i} className="text-xs text-amber-300">• {r}</li>)}</ul>)
          : <p className="text-xs text-zinc-500">Measured by the brief.</p>}
        <p className="mt-2 text-xs text-zinc-400">Claim record complete: {complete ? "yes" : "not yet"}</p>
      </Card>
      <Card title="Response latency (end of speech → first AI audio)">
        <p className="text-sm">p50 <span className="font-mono text-lg">{m?.p50 ?? "—"} s</span> · p95 <span className="font-mono text-lg">{m?.p95 ?? "—"} s</span></p>
        <p className="text-xs text-zinc-500">{m?.turns ?? 0} AI turns measured</p>
      </Card>
      <Card title="Brief quality & QA agreement">
        <p className="text-xs text-zinc-400">Rated by fraud analysts and compared with human QA reviewers during the POC (manual step).</p>
      </Card>
      <Card title="Cost per claim (estimate, list prices)">
        <p className="font-mono text-lg">${(m?.cost_usd ?? 0).toFixed(3)}</p>
        <p className="text-xs text-zinc-500">Voxtral Realtime + Mistral Large + Voxtral TTS across all calls of this claim</p>
      </Card>
    </div>
  );
}

function DecisionPanel({ c, onDone }: { c: Claim; onDone: (c: Claim) => void }) {
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const final = !!c.refund || c.decision?.outcome === "declined";
  const decide = async (outcome: string) => {
    setBusy(true);
    try { onDone(await api.decide(c.id, outcome, notes)); setNotes(""); }
    catch (e) { alert(String(e instanceof Error ? e.message : e).replace(/^\d+ /, "")); }
    finally { setBusy(false); }
  };
  return (
    <Card title="Analyst decision (human authority)" icon={<Gavel className="size-3.5" />}>
      {c.decision && (
        <div className="mb-3 rounded-lg bg-emerald-500/10 p-2.5 text-xs ring-1 ring-emerald-500/30">
          <p className="font-semibold text-emerald-200">{c.decision.outcome.replace("_", " ")}</p>
          <p className="text-zinc-300">{c.decision.notes}</p>
          <p className="text-[10px] text-zinc-500">{c.decision.analyst} · {fmtTime(c.decision.time)}</p>
          {c.refund && (
            <p className="mt-1.5 rounded bg-emerald-500/15 px-2 py-1 font-medium text-emerald-200">
              {money(c.refund.amount)} credited to account ••{c.refund.account_last4} · new balance {money(c.refund.new_balance)}
            </p>
          )}
        </div>
      )}
      {final ? (
        <p className="rounded-lg bg-zinc-800/60 p-2.5 text-[11px] text-zinc-400">
          Decision is final ({c.refund ? "money refunded" : "declined"}). The Step 4 Status Agent will tell the customer.
        </p>
      ) : (<>
      <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} placeholder="Reasoning (visible to the Status Agent in Step 4)"
        className="w-full rounded-lg bg-zinc-800 p-2 text-xs outline-none ring-1 ring-zinc-700 focus:ring-indigo-500" />
      <div className="mt-2 grid grid-cols-3 gap-1.5">
        <button disabled={busy} onClick={() => decide("refund_approved")} className="rounded-lg bg-emerald-600 py-1.5 text-[11px] font-semibold hover:bg-emerald-500 disabled:opacity-50">Refund</button>
        <button disabled={busy} onClick={() => decide("declined")} className="rounded-lg bg-rose-600 py-1.5 text-[11px] font-semibold hover:bg-rose-500 disabled:opacity-50">Decline</button>
        <button disabled={busy} onClick={() => decide("more_info")} className="rounded-lg bg-zinc-700 py-1.5 text-[11px] font-semibold hover:bg-zinc-600 disabled:opacity-50">More info</button>
      </div>
      </>)}
      <p className="mt-2 text-[10px] text-zinc-500">The AI never decides liability. The customer gets an SMS, and the Step 4 Status Agent answers from this decision.</p>
    </Card>
  );
}

function FollowupPanel({ c }: { c: Claim }) {
  const qs = (c.audit ?? []).filter((a) => a.action === "followup.question");
  return (
    <Card title="Step 4 · Follow-up calls" icon={<AlertTriangle className="size-3.5" />}>
      {c.followups.length === 0 && <p className="text-xs text-zinc-500">When the customer calls back, the Status Agent answers from this record. Call again from the phone to try it.</p>}
      {c.followups.map((f) => (
        <div key={f.call_id} className="mb-2 rounded-lg bg-zinc-800/50 p-2 text-xs">
          <p className="font-mono text-[10px] text-zinc-500">{fmtTime(f.time)} · {f.call_id}</p>
          <p className="text-zinc-300">{f.summary ?? "Call logged"}</p>
        </div>
      ))}
      {qs.map((q) => <p key={q.id} className="mt-1 text-xs text-amber-300">Question for analyst: {q.detail}</p>)}
    </Card>
  );
}
