// Right-hand panel: call details while live, then the saved JSON summary after the call.
import clsx from "clsx";
import {
  AlertTriangle,
  CheckCircle2,
  CreditCard,
  Download,
  ExternalLink,
  HelpCircle,
  Loader2,
  ListChecks,
  Quote,
  XCircle,
} from "lucide-react";
import { fmtDuration, type Call, type Evidence } from "../lib/api";

const FACT_LABELS: Record<string, string> = {
  contacted_by_impersonator: "Contacted by impersonator",
  shared_otp_or_pin: "Shared OTP / PIN",
  saw_bank_warning: "Saw bank warning",
};

function jumpToLine(callId: string, line: number | null) {
  if (line === null || line === undefined) return;
  const el = document.getElementById(`line-${callId}-${line}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth; // restart animation
  el.classList.add("flash");
}

export default function SummaryPanel({ call }: { call: Call }) {
  const s = call.summary;
  const jsonUrl = `/api/calls/${call.call_id}/record`;
  const duration = call.answered_at && call.ended_at ? fmtDuration(call.ended_at - call.answered_at) : "—";

  return (
    <div className="space-y-4">
      <Card title="Call record">
        <Row k="Call ID" v={<span className="font-mono text-xs">{call.call_id}</span>} />
        <Row k="Caller" v={call.caller_name} />
        <Row k="Number" v={<span className="font-mono text-xs">{call.caller_number}</span>} />
        <Row k="Started" v={new Date(call.created_at * 1000).toLocaleTimeString()} />
        <Row k="Duration" v={duration} />
        <Row k="Lines" v={call.lines.length} />
      </Card>

      {call.status === "wrapping_up" && (
        <div className="flex items-center gap-2 rounded-xl bg-indigo-500/10 p-3 text-sm text-indigo-200 ring-1 ring-indigo-500/30">
          <Loader2 className="size-4 animate-spin" />
          Transcript saved. Mistral is writing the summary…
        </div>
      )}

      {["active", "connecting"].includes(call.status) && (
        <p className="rounded-xl border border-dashed border-zinc-800 p-3 text-xs text-zinc-500">
          The summary is generated automatically when the call ends.
        </p>
      )}

      {s && (
        <>
          {s.error && (
            <p className="rounded-xl bg-rose-500/10 p-3 text-xs text-rose-300 ring-1 ring-rose-500/30">{s.error}</p>
          )}
          {s.short_summary && (
            <Card title="Summary">
              <p className="text-sm leading-relaxed text-zinc-200">{s.short_summary}</p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {s.call_reason && <Chip>{s.call_reason.replaceAll("_", " ")}</Chip>}
                {s.customer_sentiment && <Chip>{s.customer_sentiment}</Chip>}
                {s.caller?.verified !== undefined && (
                  <Chip tone={s.caller.verified ? "green" : "amber"}>
                    {s.caller.verified ? "verified" : "not verified"}
                    {s.caller.verification_method ? ` · ${s.caller.verification_method}` : ""}
                  </Chip>
                )}
              </div>
            </Card>
          )}

          {s.card && (s.card.last_four || s.card.block_requested) && (
            <Card title="Card" icon={<CreditCard className="size-3.5" />}>
              <Row k="Last four" v={<span className="font-mono">{s.card.last_four ?? "—"}</span>} />
              <Row k="Block requested" v={<YesNo v={s.card.block_requested} />} />
              <Row k="Confirmed by read-back" v={<YesNo v={s.card.block_confirmed_by_readback} />} />
            </Card>
          )}

          {!!s.disputed_transactions?.length && (
            <Card title="Disputed transactions">
              <div className="space-y-1.5">
                {s.disputed_transactions.map((t, i) => (
                  <div key={i} className="flex items-center justify-between rounded-lg bg-zinc-800/60 px-2.5 py-1.5 text-xs">
                    <span className="truncate text-zinc-300">{t.merchant_or_payee ?? "Unknown payee"}{t.when ? ` · ${t.when}` : ""}</span>
                    <span className="shrink-0 whitespace-nowrap font-mono font-semibold">
                      {t.amount != null ? `${t.currency ?? ""} ${t.amount.toLocaleString()}` : "—"}
                    </span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {s.liability_facts && (
            <Card title="Liability facts (with evidence)">
              {s.how_it_happened && <p className="mb-3 text-xs text-zinc-400">{s.how_it_happened}</p>}
              <div className="space-y-2">
                {Object.entries(s.liability_facts).map(([k, f]) => (
                  <Fact key={k} label={FACT_LABELS[k] ?? k} f={f} onJump={() => jumpToLine(call.call_id, f?.line)} />
                ))}
              </div>
              <p className="mt-3 text-[10px] text-zinc-500">Prompts for the analyst, not findings. The AI does not decide liability.</p>
            </Card>
          )}

          {!!s.vulnerability_cues?.length && (
            <Card title="Vulnerability cues" icon={<AlertTriangle className="size-3.5 text-amber-400" />}>
              {s.vulnerability_cues.map((v, i) => (
                <button key={i} onClick={() => jumpToLine(call.call_id, v.line)}
                  className="block w-full rounded-lg bg-amber-500/10 p-2 text-left text-xs text-amber-200 hover:bg-amber-500/20">
                  {v.cue} <span className="text-amber-400/60">— “{v.evidence}” #{v.line}</span>
                </button>
              ))}
            </Card>
          )}

          <ListCard title="Actions taken" items={s.actions_taken} />
          <ListCard title="Next steps" items={s.next_steps} />
          <ListCard title="Missing information" items={s.missing_information} tone="amber" />
        </>
      )}

      {call.file && call.status === "ended" && (
        <div className="flex gap-2">
          <a href={jsonUrl} target="_blank" rel="noreferrer"
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-zinc-800 py-2 text-xs font-medium hover:bg-zinc-700">
            <ExternalLink className="size-3.5" /> Open JSON
          </a>
          <a href={jsonUrl} download={call.file}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-zinc-800 py-2 text-xs font-medium hover:bg-zinc-700">
            <Download className="size-3.5" /> Download
          </a>
        </div>
      )}
      {call.file && <p className="text-center font-mono text-[10px] text-zinc-600">saved to calls/{call.file}</p>}
    </div>
  );
}

function Card({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-zinc-900 p-3.5 ring-1 ring-zinc-800">
      <h3 className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
        {icon}
        {title}
      </h3>
      {children}
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-0.5 text-sm">
      <span className="text-zinc-500">{k}</span>
      <span className="text-zinc-200">{v}</span>
    </div>
  );
}

function Chip({ children, tone }: { children: React.ReactNode; tone?: "green" | "amber" }) {
  return (
    <span className={clsx("rounded-full px-2 py-0.5 text-[11px] capitalize ring-1",
      tone === "green" ? "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30"
        : tone === "amber" ? "bg-amber-500/10 text-amber-300 ring-amber-500/30"
        : "bg-zinc-800 text-zinc-300 ring-zinc-700")}>
      {children}
    </span>
  );
}

function YesNo({ v }: { v?: boolean | null }) {
  if (v === true) return <CheckCircle2 className="size-4 text-emerald-400" />;
  if (v === false) return <XCircle className="size-4 text-zinc-500" />;
  return <HelpCircle className="size-4 text-zinc-600" />;
}

function Fact({ label, f, onJump }: { label: string; f: Evidence; onJump: () => void }) {
  const value = f?.value;
  return (
    <div className="rounded-lg bg-zinc-800/50 p-2.5">
      <div className="flex items-center justify-between text-sm">
        <span className="text-zinc-300">{label}</span>
        <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-semibold",
          value === true ? "bg-rose-500/15 text-rose-300" : value === false ? "bg-emerald-500/15 text-emerald-300" : "bg-zinc-700 text-zinc-400")}>
          {value === true ? "YES" : value === false ? "NO" : "UNKNOWN"}
        </span>
      </div>
      {f?.evidence && (
        <button onClick={onJump} className="mt-1.5 flex w-full items-start gap-1.5 text-left text-xs text-zinc-400 hover:text-zinc-200">
          <Quote className="mt-0.5 size-3 shrink-0" />
          <span>“{f.evidence}” <span className="text-zinc-600">#{f.line}</span></span>
        </button>
      )}
    </div>
  );
}

function ListCard({ title, items, tone }: { title: string; items?: string[]; tone?: "amber" }) {
  if (!items?.length) return null;
  return (
    <Card title={title} icon={<ListChecks className="size-3.5" />}>
      <ul className="space-y-1">
        {items.map((it, i) => (
          <li key={i} className={clsx("text-xs", tone === "amber" ? "text-amber-200" : "text-zinc-300")}>• {it}</li>
        ))}
      </ul>
    </Card>
  );
}
