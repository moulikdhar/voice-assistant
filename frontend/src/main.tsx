import { StrictMode, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { RotateCcw, ShieldCheck, Wifi, WifiOff } from "lucide-react";
import "./index.css";
import Phone from "./components/Phone";
import Console from "./components/Console";
import InterviewDesk from "./pages/InterviewDesk";
import Investigator from "./pages/Investigator";
import { LiveProvider, useLive } from "./lib/live";
import { api } from "./lib/api";

/**
 * The customer's phone stays on the left on EVERY bank screen (it is never unmounted, so the call keeps
 * going while the bank side moves from the console to the interview desk to the investigator).
 */
function WithPhone() {
  return (
    <div className="flex h-full gap-4 p-4 pr-0">
      <div className="flex shrink-0 flex-col justify-center"><Phone /></div>
      <div className="min-w-0 flex-1 [&>*]:h-full"><Outlet /></div>
    </div>
  );
}

/** Demo autopilot: follow the claim through the bank screens. */
function AutoFollow() {
  const { handovers, callList, resetCount } = useLive();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const pathRef = useRef(pathname);
  pathRef.current = pathname;
  const seen = useRef<Set<string>>(new Set(Object.keys(handovers)));
  const prevStatus = useRef<Record<string, string> | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const later = (fn: () => void, ms: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(fn, ms);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => { prevStatus.current = null; }, [resetCount]);

  // Step 1 -> Step 2: the AI transfers the call -> open the specialist desk after 2 s
  useEffect(() => {
    for (const id of Object.keys(handovers)) {
      if (seen.current.has(id)) continue;
      seen.current.add(id);
      later(() => { if (pathRef.current !== "/interview") navigate("/interview"); }, 2000);
    }
  }, [handovers]);

  // Step 2 -> Step 3: an interview call goes from active to ended -> open its claim in the investigator
  useEffect(() => {
    const before = prevStatus.current;
    const now: Record<string, string> = {};
    for (const c of callList) now[c.call_id] = c.status;
    prevStatus.current = now;
    if (!before) return; // first snapshot after load/reset: nothing "just ended"
    for (const c of callList) {
      if (c.phase === "interview" && c.mode === "intake" && before[c.call_id] === "active" && c.status !== "active"
          && c.claim_id && pathRef.current === "/interview") {
        const id = c.claim_id;
        later(() => navigate(`/investigate?claim=${id}`), 2500);
      }
    }
  }, [callList]);
  return null;
}

function Calls() {
  return <Console />;
}

const TABS = [
  { to: "/", n: "1 · 4", label: "Report & Follow-up", hint: "phone + AI voice agent" },
  { to: "/interview", n: "2", label: "Interview", hint: "specialist desk" },
  { to: "/investigate", n: "3", label: "Investigate", hint: "brief, QA, decision" },
];

function TopNav() {
  const { connected, claimList } = useLive();
  const latest = claimList[0];
  return (
    <nav className="flex h-12 shrink-0 items-center gap-2 border-b border-zinc-800 bg-zinc-950 px-4">
      <ShieldCheck className="size-5 text-emerald-400" />
      <span className="mr-3 whitespace-nowrap text-sm font-semibold">Northbridge Bank <span className="hidden font-normal text-zinc-500 2xl:inline">· Fraud & Dispute Claims POC</span></span>
      {TABS.map((t) => (
        <NavLink key={t.to} to={t.to} end
          className={({ isActive }) => clsx("flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs transition",
            isActive ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200")}>
          <span className="rounded bg-zinc-700 px-1.5 py-0.5 font-mono text-[10px]">{t.n}</span>
          {t.label}<span className="hidden text-zinc-500 2xl:inline">· {t.hint}</span>
        </NavLink>
      ))}
      {latest && (
        <span className="ml-3 hidden truncate whitespace-nowrap font-mono text-[11px] text-zinc-500 xl:inline">
          latest claim {latest.reference} · {latest.status}
        </span>
      )}
      <div className="ml-auto flex shrink-0 items-center gap-3 whitespace-nowrap text-xs">
        <button onClick={() => confirm("Reset the demo? This deletes all calls, claims and messages.") && void api.reset().catch(() => {})}
          className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200">
          <RotateCcw className="size-3.5" /> Reset demo
        </button>
        <span className={clsx("flex items-center gap-1.5", connected ? "text-emerald-400" : "text-rose-400")}>
          {connected ? <Wifi className="size-3.5" /> : <WifiOff className="size-3.5" />}{connected ? "Live" : "Reconnecting…"}
        </span>
      </div>
    </nav>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col">
      <TopNav />
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LiveProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell><AutoFollow /><WithPhone /></Shell>}>
            <Route path="/" element={<Calls />} />
            <Route path="/interview" element={<InterviewDesk />} />
            <Route path="/investigate" element={<Investigator />} />
          </Route>
          <Route path="/phone" element={<div className="grid h-full place-items-center"><Phone /></div>} />
          <Route path="/console" element={<Shell><div className="h-full p-4"><Console /></div></Shell>} />
        </Routes>
      </BrowserRouter>
    </LiveProvider>
  </StrictMode>,
);
