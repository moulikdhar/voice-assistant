// Right-hand panel on the console: the CLAIM RECORD filling up live as the AI uses the bank tools.
import { Link } from "react-router-dom";
import clsx from "clsx";
import { CheckCircle2, Circle, CreditCard, FileCheck2, Hourglass, KeyRound, ListChecks, MessageSquareText, Timer } from "lucide-react";
import { fmtTime, money, type Call } from "../lib/api";
import { useLive } from "../lib/live";

export default function LiveClaimPanel({ call }: { call: Call }) {
  const { claims, audit } = useLive();
  const claim = call.claim_id ? claims[call.claim_id] : undefined;
  const v = call.verification ?? {};
  const turns = call.metrics?.turns ?? [];
  const last = turns.at(-1);
  const events = audit.filter((a) => a.call_id === call.call_id).slice(-8).reverse();

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className={clsx("rounded-full px-2.5 py-1 text-[11px] font-semibold",
          call.mode === "status" ? "bg-amber-500/15 text-amber-300" : "bg-indigo-500/15 text-indigo-300")}>
          {call.mode === "status" ? "Step 4 · Status Agent (read-only)" : "Step 1 · Claim Intake Agent"}
        </span>
      </div>

      <Card title="Claim record (live)" icon={<FileCheck2 className="size-3.5" />}>
        <Check ok={!!v.otp_sent} label={v.otp_sent ? `OTP sent to mobile ending ${v.mobile_ending ?? ""}` : "OTP not sent yet"} icon={<KeyRound className="size-3.5" />} />
        <Check ok={!!v.verified} label={v.verified ? `Verified · ${v.method ?? "OTP"}` : `Not verified${v.attempts ? ` (${v.attempts} attempt${v.attempts > 1 ? "s" : ""})` : ""}`} />
        {call.card_actions.map((a, i) => (
          <Check key={i} ok label={`Card ending ${a.card_last4} blocked after read-back`} icon={<CreditCard className="size-3.5" />} />
        ))}
        {call.pending.map((p) => (
          <div key={p.id} className="mt-1.5 rounded-lg bg-amber-500/10 p-2 text-xs text-amber-200 ring-1 ring-amber-500/30">
            <p className="flex items-center gap-1.5 font-semibold"><Hourglass className="size-3.5" /> Waiting for the customer's yes</p>
            <p className="mt-1 text-amber-100/80">“{p.read_back}”</p>
          </div>
        ))}
        {claim ? (
          <div className="mt-2 rounded-lg bg-emerald-500/10 p-2.5 ring-1 ring-emerald-500/30">
            <p className="font-mono text-sm font-semibold text-emerald-200">{claim.reference}</p>
            <p className="text-xs text-zinc-300">{claim.disputed_transactions.length} payments · {money(claim.total_disputed, claim.currency)}</p>
            <p className="mt-1 flex items-center gap-1.5 text-xs text-zinc-400">
              <Timer className="size-3.5" /> Deadline {fmtTime(claim.deadline_at)} · {claim.clock?.business_days_left ?? "—"} business days left
            </p>
            <Link to={`/investigate?claim=${claim.id}`} className="mt-1.5 inline-block text-[11px] text-emerald-300 hover:underline">Open claim →</Link>
          </div>
        ) : (
          <Check ok={false} label="No claim created yet" />
        )}
      </Card>

      {events.length > 0 && (
        <Card title="Actions & audit" icon={<ListChecks className="size-3.5" />}>
          <ul className="space-y-1.5">
            {events.map((e) => (
              <li key={e.id} className="text-xs">
                <span className="font-mono text-[10px] text-zinc-500">{new Date(e.time).toLocaleTimeString()}</span>{" "}
                <span className="text-zinc-400">{e.actor}</span> · <span className="text-zinc-200">{e.detail || e.action}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {last && (
        <Card title="Response latency" icon={<MessageSquareText className="size-3.5" />}>
          <p className="text-xs text-zinc-400">End of speech → first AI audio, last turn</p>
          <p className="font-mono text-lg text-zinc-100">{last.total.toFixed(2)} s</p>
          <p className="text-[11px] text-zinc-500">turn detection {last.eou.toFixed(2)} · Mistral first token {last.llm_ttft.toFixed(2)} · Voxtral TTS first audio {last.tts_ttfb.toFixed(2)}</p>
        </Card>
      )}
    </div>
  );
}

export function Card({ title, icon, children, className }: { title: string; icon?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={clsx("rounded-xl bg-zinc-900 p-3.5 ring-1 ring-zinc-800", className)}>
      <h3 className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">{icon}{title}</h3>
      {children}
    </div>
  );
}

function Check({ ok, label, icon }: { ok: boolean; label: string; icon?: React.ReactNode }) {
  return (
    <p className={clsx("flex items-center gap-2 py-0.5 text-sm", ok ? "text-zinc-100" : "text-zinc-500")}>
      {ok ? <CheckCircle2 className="size-4 shrink-0 text-emerald-400" /> : <Circle className="size-4 shrink-0 text-zinc-600" />}
      {icon && <span className="text-zinc-500">{icon}</span>}
      {label}
    </p>
  );
}
