// "Customer Phone": a small interactive phone (home screen, dialer, in-call screen, Messages, bank app).
// You dial the bank, hear it ring and talk to the AI. SMS (OTP, claim reference, decision) arrive as
// notifications you can tap. The call keeps running while you use other apps: tap the green pill to return.
import { useEffect, useMemo, useRef, useState } from "react";
import { ParticipantKind, Room, RoomEvent, type RemoteParticipant } from "livekit-client";
import {
  BarVisualizer, RoomAudioRenderer, RoomContext, StartAudio, useTranscriptions, useVoiceAssistant,
} from "@livekit/components-react";
import { motion, AnimatePresence } from "motion/react";
import {
  BatteryMedium, ChevronLeft, CircleDot, Clock3, Copy, CreditCard, Delete, Grid3x3, Landmark, MessageSquare, Mic, MicOff,
  Phone as PhoneIcon, PhoneIncoming, PhoneMissed, PhoneOff, Settings as SettingsIcon, ShieldCheck, Signal, UserRound,
  Volume2, Wifi,
} from "lucide-react";
import clsx from "clsx";
import { api, digitsOnly, fmtDuration, money, type Call, type Claim, type Sms, type Txn } from "../lib/api";
import { endTone, keyTone, smsTone, startRingback, unlockAudio } from "../lib/tones";
import { useLive, useNow } from "../lib/live";

const BANK_NUMBER = "1800 266 0101";
const BANK_SENDER = "NB-BANK";

type PhoneState = "idle" | "ringing" | "connected" | "ended";
type Screen = "home" | "dialer" | "call" | "messages" | "thread" | "bank" | "settings";

interface Customer { id: string; name: string; phone: string; segment?: string; account?: { last4: string; type: string; balance: number }; cards: { last4: string; type: string; status: string }[] }

export default function Phone() {
  // ── call state ──
  const [state, setState] = useState<PhoneState>("idle");
  const [room, setRoom] = useState<Room | null>(null);
  const [callId, setCallId] = useState<string | null>(null);
  const [answeredAt, setAnsweredAt] = useState<number | null>(null);
  const [muted, setMuted] = useState(false);
  const [endText, setEndText] = useState("Call ended");
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("Amit Sharma");
  const [number, setNumber] = useState("+91 98765 43210");
  const stopRing = useRef<(() => void) | null>(null);
  const endedRef = useRef(false);
  const answeredRef = useRef(false);
  const roomRef = useRef<Room | null>(null);      // the live room, even before React state catches up
  const callIdRef = useRef<string | null>(null);
  const endTimer = useRef<number | undefined>(undefined);
  useEffect(() => { answeredRef.current = answeredAt !== null; }, [answeredAt]);

  // ── navigation (a stack, so the Back button works like a real phone) ──
  const [stack, setStack] = useState<Screen[]>(["home"]);
  const screen = stack[stack.length - 1];
  const go = (s: Screen) => setStack((st) => (st[st.length - 1] === s ? st : [...st, s]));
  const back = () => setStack((st) => (st.length > 1 ? st.slice(0, -1) : st));
  const home = () => setStack(["home"]);

  // ── data ──
  const live = useLive();
  const myCall = callId ? live.calls[callId] : undefined;
  const mySms = useMemo(() => live.sms.filter((m) => digitsOnly(m.phone) === digitsOnly(number)), [live.sms, number]);
  const [readUpTo, setReadUpTo] = useState(() => Date.now());
  const unread = mySms.filter((m) => new Date(m.time).getTime() > readUpTo).length;
  // SMS times are whole seconds: keep a 1 s margin so a message that just arrived is not marked read
  useEffect(() => { if (screen === "thread") setReadUpTo(Date.now() - 1000); }, [screen, mySms.length]);
  useEffect(() => { if (live.resetCount) { setReadUpTo(Date.now()); } }, [live.resetCount]);

  const inCall = state === "ringing" || state === "connected";

  const resetCall = () => {
    window.clearTimeout(endTimer.current);
    void roomRef.current?.disconnect();
    roomRef.current = null;
    callIdRef.current = null;
    setState("idle");
    setRoom(null);
    setCallId(null);
    setAnsweredAt(null);
    setMuted(false);
  };

  const finish = (text: string) => {
    if (endedRef.current) return;
    endedRef.current = true;
    stopRing.current?.();
    stopRing.current = null;
    endTone();
    setEndText(text);
    setState("ended");
    window.clearTimeout(endTimer.current);
    endTimer.current = window.setTimeout(() => {
      resetCall();
      // leave the call screen wherever it is in the stack (Back must not land on "No active call")
      setStack((st) => { const f = st.filter((x) => x !== "call"); return f.length ? f : ["dialer"]; });
    }, 2500);
  };
  useEffect(() => () => window.clearTimeout(endTimer.current), []);

  async function dial() {
    if (inCall) return go("call");
    if (state === "ended") resetCall(); // calling again during the "Call ended" screen
    unlockAudio();
    setError(null);
    endedRef.current = false;
    answeredRef.current = false;
    setState("ringing");
    go("call");
    stopRing.current = startRingback();
    let r: Room | null = null;
    let id: string | null = null;
    try {
      const { call, livekit_url, token } = await api.startCall(name, number);
      id = call.call_id;
      callIdRef.current = id;
      if (endedRef.current) { void api.hangup(id).catch(() => {}); return; } // hung up while dialling
      setCallId(id);
      r = new Room({ adaptiveStream: true });
      roomRef.current = r;
      const room_ = r;
      const onAgent = (p: RemoteParticipant) => {
        if (p.kind !== ParticipantKind.AGENT || endedRef.current) return;
        stopRing.current?.();
        stopRing.current = null;
        answeredRef.current = true;
        setAnsweredAt(Date.now() / 1000);
        setState("connected");
      };
      room_.on(RoomEvent.ParticipantConnected, onAgent);
      room_.on(RoomEvent.ParticipantDisconnected, (p) => {
        if (p.kind === ParticipantKind.AGENT) {
          finish("Call ended");
          void room_.disconnect();
        }
      });
      room_.on(RoomEvent.Disconnected, () => finish(answeredRef.current ? "Call ended" : "Call not answered"));
      await room_.connect(livekit_url, token);
      if (endedRef.current) { await room_.disconnect(); return; }
      await room_.localParticipant.setMicrophoneEnabled(true, { echoCancellation: true, noiseSuppression: true, autoGainControl: true });
      if (endedRef.current) { await room_.disconnect(); return; }
      room_.remoteParticipants.forEach(onAgent);
      setRoom(room_);
    } catch (e) {
      endedRef.current = true;
      stopRing.current?.();
      stopRing.current = null;
      void r?.disconnect();
      roomRef.current = null;
      if (id) void api.hangup(id).catch(() => {});
      setCallId(null);
      const msg = e instanceof Error ? e.message : String(e);
      setError(/permission|NotAllowed/i.test(msg) ? "Microphone blocked: allow the mic for this page and try again." : msg);
      setState("idle");
      setStack((st) => { const f = st.filter((x) => x !== "call"); return f.length ? f : ["dialer"]; });
    }
  }

  async function hangup() {
    const id = callIdRef.current ?? callId;
    finish("Call ended");
    if (id) void api.hangup(id).catch(() => {});
    await roomRef.current?.disconnect();
  }

  // the backend is the source of truth: if it ends / misses the call, the phone ends it too
  useEffect(() => {
    if (inCall && myCall && ["missed", "declined", "ended"].includes(myCall.status)) {
      finish(answeredRef.current ? "Call ended" : "Call not answered");
      void roomRef.current?.disconnect();
    }
  }, [myCall?.status]);
  // never ring forever (e.g. the AI agent is not running)
  useEffect(() => {
    if (state !== "ringing") return;
    const t = window.setTimeout(() => void hangup(), 45000);
    return () => window.clearTimeout(t);
  }, [state]);

  async function toggleMute() {
    if (!room) return;
    await room.localParticipant.setMicrophoneEnabled(muted);
    setMuted(!muted);
  }

  useEffect(() => () => void roomRef.current?.disconnect(), []);

  return (
    <div className="mx-auto w-[340px] shrink-0">
      <div className="relative h-[680px] overflow-hidden rounded-[3rem] border-[10px] border-zinc-800 bg-black shadow-2xl shadow-black/60 ring-1 ring-zinc-700">
        {/* audio keeps playing whatever app is open */}
        {room && (
          <RoomContext.Provider value={room}>
            <RoomAudioRenderer />
          </RoomContext.Provider>
        )}
        <StatusBar inCall={inCall && screen !== "call"} answeredAt={answeredAt} ringing={state === "ringing"} onReturn={() => go("call")} />
        <SmsBanner messages={mySms} onOpen={() => go("thread")} />

        <div className="absolute inset-x-0 bottom-10 top-9 overflow-hidden">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={screen} className="h-full"
              initial={{ opacity: 0, x: 18 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -18 }} transition={{ duration: 0.16 }}>
              {screen === "home" && <HomeScreen unread={unread} onOpen={go} onCallBank={dial} lastSms={mySms.at(-1)} />}
              {screen === "dialer" && (
                <Dialer onCall={dial} error={error} name={name} number={number} inCall={inCall}
                  recents={live.callList.filter((c) => digitsOnly(c.caller_number) === digitsOnly(number))} />
              )}
              {screen === "call" && (
                <CallScreen state={state} answeredAt={answeredAt} endText={endText} muted={muted} room={room}
                  call={myCall} onMute={toggleMute} onHangup={hangup} onMessages={() => go("thread")} unread={unread} />
              )}
              {screen === "messages" && <MessagesList messages={mySms} unread={unread} onOpen={() => go("thread")} />}
              {screen === "thread" && <Thread messages={mySms} />}
              {screen === "bank" && <BankApp number={number} onCall={() => go("dialer")} />}
              {screen === "settings" && <SettingsScreen name={name} number={number} setName={setName} setNumber={setNumber} locked={inCall} />}
            </motion.div>
          </AnimatePresence>
        </div>

        <NavBar onBack={back} onHome={home} canBack={stack.length > 1} />
      </div>
      <p className="mt-3 text-center text-xs text-zinc-500">Customer phone (simulated) · use headphones</p>
    </div>
  );
}

/* ───────────────────────────── chrome ───────────────────────────── */
function StatusBar({ inCall, answeredAt, ringing, onReturn }: { inCall: boolean; answeredAt: number | null; ringing: boolean; onReturn: () => void }) {
  const now = useNow(1000);
  const t = new Date(now * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return (
    <div className="absolute inset-x-0 top-0 z-30 flex h-9 items-center justify-between px-6 text-[11px] font-semibold text-zinc-100">
      {inCall ? (
        <button onClick={onReturn} className="flex items-center gap-1 rounded-full bg-emerald-500 px-2 py-0.5 text-[10px] text-white">
          <PhoneIcon className="size-2.5 fill-white" /> {ringing ? "Calling…" : fmtDuration(now - (answeredAt ?? now))}
        </button>
      ) : <span>{t}</span>}
      <div className="absolute left-1/2 top-2 h-5 w-24 -translate-x-1/2 rounded-full bg-black ring-1 ring-zinc-900" />
      <span className="flex items-center gap-1"><Signal className="size-3" /><Wifi className="size-3" /><BatteryMedium className="size-4" /></span>
    </div>
  );
}

function NavBar({ onBack, onHome, canBack }: { onBack: () => void; onHome: () => void; canBack: boolean }) {
  return (
    <div className="absolute inset-x-0 bottom-0 z-30 flex h-10 items-center justify-around border-t border-zinc-900 bg-black/90 px-10">
      <button onClick={onBack} disabled={!canBack} title="Back"
        className="grid size-8 place-items-center rounded-full text-zinc-300 transition hover:bg-zinc-800 disabled:opacity-30">
        <ChevronLeft className="size-5" />
      </button>
      <button onClick={onHome} title="Home" className="grid size-8 place-items-center rounded-full text-zinc-300 transition hover:bg-zinc-800">
        <CircleDot className="size-4" />
      </button>
    </div>
  );
}

/** Notification for a new SMS. Tap it to open the message. */
function SmsBanner({ messages, onOpen }: { messages: Sms[]; onOpen: () => void }) {
  const [shown, setShown] = useState<Sms | null>(null);
  const mountedAt = useRef<number>(Date.now());
  const lastId = useRef<string | null>(null);
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (!last || last.id === lastId.current) return;
    lastId.current = last.id;
    if (new Date(last.time).getTime() < mountedAt.current - 2000) return;
    setShown(last);
    smsTone();
  }, [messages]);
  useEffect(() => {
    if (!shown) return;
    const t = window.setTimeout(() => setShown(null), 9000);
    return () => window.clearTimeout(t);
  }, [shown]);
  const code = shown ? otpOf(shown.text) : null;
  return (
    <AnimatePresence>
      {shown && (
        <motion.button
          initial={{ y: -50, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -50, opacity: 0 }}
          onClick={() => { setShown(null); onOpen(); }}
          className="absolute inset-x-2.5 top-10 z-40 rounded-2xl bg-zinc-800/95 p-3 text-left shadow-xl ring-1 ring-zinc-700 backdrop-blur hover:bg-zinc-700/95">
          <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-400">
            <span className="grid size-4 place-items-center rounded bg-emerald-500"><MessageSquare className="size-2.5 text-white" /></span>
            Messages · {BANK_SENDER} · now
          </p>
          {code && <p className="mt-1 font-mono text-lg font-bold tracking-[0.25em] text-emerald-300">{code}</p>}
          <p className="mt-0.5 line-clamp-2 text-xs leading-snug text-zinc-100">{shown.text}</p>
          <p className="mt-1 text-[10px] text-zinc-500">Tap to open</p>
        </motion.button>
      )}
    </AnimatePresence>
  );
}

const otpOf = (text: string) => /verification code is (\d{6})/i.exec(text)?.[1] ?? null;
const smsTime = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/* ───────────────────────────── home ───────────────────────────── */
function HomeScreen({ unread, onOpen, onCallBank, lastSms }: { unread: number; onOpen: (s: Screen) => void; onCallBank: () => void; lastSms?: Sms }) {
  const now = useNow(1000);
  const d = new Date(now * 1000);
  const apps: { id: Screen; label: string; icon: React.ReactNode; bg: string; badge?: number }[] = [
    { id: "dialer", label: "Phone", icon: <PhoneIcon className="size-6 fill-white text-white" />, bg: "bg-emerald-500" },
    { id: "messages", label: "Messages", icon: <MessageSquare className="size-6 text-white" />, bg: "bg-sky-500", badge: unread },
    { id: "bank", label: "NB Bank", icon: <Landmark className="size-6 text-white" />, bg: "bg-indigo-600" },
    { id: "settings", label: "Settings", icon: <SettingsIcon className="size-6 text-white" />, bg: "bg-zinc-600" },
  ];
  return (
    <div className="flex h-full flex-col bg-[radial-gradient(ellipse_at_top,_#1e3a5f_0%,_#09090b_65%)] px-5 pt-6">
      <p className="text-center font-light text-6xl tracking-tight">{d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}</p>
      <p className="mt-1 text-center text-sm text-zinc-300">{d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })}</p>

      <button onClick={onCallBank} className="mt-6 flex items-center gap-3 rounded-2xl bg-white/10 p-3 text-left ring-1 ring-white/10 backdrop-blur transition hover:bg-white/15">
        <span className="grid size-10 place-items-center rounded-xl bg-emerald-500/20"><ShieldCheck className="size-5 text-emerald-300" /></span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs text-zinc-300">Northbridge Bank · Fraud helpline</span>
          <span className="block font-mono text-sm font-semibold">{BANK_NUMBER}</span>
        </span>
        <span className="grid size-9 place-items-center rounded-full bg-emerald-500"><PhoneIcon className="size-4 fill-white text-white" /></span>
      </button>

      {lastSms && (
        <button onClick={() => onOpen("thread")} className="mt-3 rounded-2xl bg-white/5 p-3 text-left ring-1 ring-white/10 hover:bg-white/10">
          <p className="text-[10px] uppercase tracking-wide text-zinc-400">Latest message · {smsTime(lastSms.time)}</p>
          <p className="mt-0.5 line-clamp-2 text-xs text-zinc-200">{lastSms.text}</p>
        </button>
      )}

      <div className="mt-auto grid grid-cols-4 gap-3 pb-5">
        {apps.map((a) => (
          <button key={a.id} onClick={() => onOpen(a.id)} className="flex flex-col items-center gap-1.5">
            <span className={clsx("relative grid size-14 place-items-center rounded-2xl shadow-lg transition hover:scale-105 active:scale-95", a.bg)}>
              {a.icon}
              {!!a.badge && <span className="absolute -right-1 -top-1 grid min-w-5 place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-bold">{a.badge}</span>}
            </span>
            <span className="text-[11px] text-zinc-200">{a.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ───────────────────────────── dialer ───────────────────────────── */
const KEYS: [string, string][] = [["1", ""], ["2", "ABC"], ["3", "DEF"], ["4", "GHI"], ["5", "JKL"], ["6", "MNO"],
  ["7", "PQRS"], ["8", "TUV"], ["9", "WXYZ"], ["*", ""], ["0", "+"], ["#", ""]];

function Keypad({ onKey }: { onKey: (k: string) => void }) {
  return (
    <div className="grid grid-cols-3 gap-x-6 gap-y-3">
      {KEYS.map(([k, l]) => (
        <button key={k} onClick={() => { keyTone(k); onKey(k); }}
          className="grid size-16 place-items-center rounded-full bg-zinc-800/80 transition hover:bg-zinc-700 active:scale-95 active:bg-zinc-600">
          <span className="text-2xl leading-none">{k}</span>
          <span className="-mt-3 text-[9px] tracking-widest text-zinc-400">{l || " "}</span>
        </button>
      ))}
    </div>
  );
}

function Dialer({ onCall, error, name, number, inCall, recents }: { onCall: () => void; error: string | null; name: string; number: string; inCall: boolean; recents: Call[] }) {
  const [tab, setTab] = useState<"keypad" | "recents">("keypad");
  const [typed, setTyped] = useState(BANK_NUMBER.replace(/\s/g, ""));
  const pretty = typed.replace(/^(\d{4})(\d{3})(\d{0,4}).*$/, "$1 $2 $3").trim();
  const isBank = typed === BANK_NUMBER.replace(/\s/g, "");
  return (
    <div className="flex h-full flex-col px-5 pt-3">
      <div className="mx-auto flex rounded-full bg-zinc-900 p-0.5 text-xs">
        {(["keypad", "recents"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={clsx("rounded-full px-4 py-1 capitalize", tab === t ? "bg-zinc-700 text-white" : "text-zinc-400")}>{t}</button>
        ))}
      </div>

      {tab === "keypad" ? (
        <>
          <div className="mt-5 text-center">
            <p className="h-9 truncate font-mono text-3xl tracking-wider">{pretty || " "}</p>
            <p className="mt-1 h-4 text-xs text-emerald-400">{isBank ? "Northbridge Bank · Fraud & Disputes" : ""}</p>
          </div>
          <div className="mt-4 flex justify-center"><Keypad onKey={(k) => setTyped((s) => (s + k).slice(0, 14))} /></div>
          <div className="mt-4 grid grid-cols-3 items-center justify-items-center">
            <span />
            <button onClick={() => (isBank ? onCall() : undefined)} disabled={!isBank && !inCall}
              title={isBank ? "Call" : "In this demo only the bank's helpline can be called"}
              className="grid size-16 place-items-center rounded-full bg-emerald-500 shadow-lg shadow-emerald-500/30 transition hover:scale-105 hover:bg-emerald-400 active:scale-95 disabled:opacity-40">
              <PhoneIcon className="size-7 fill-white text-white" />
            </button>
            <button onClick={() => setTyped((s) => s.slice(0, -1))} className="grid size-10 place-items-center text-zinc-400 hover:text-white"><Delete className="size-6" /></button>
          </div>
          {!isBank && <button onClick={() => setTyped(BANK_NUMBER.replace(/\s/g, ""))} className="mt-2 text-center text-[11px] text-sky-400">Use the bank's helpline number</button>}
          {error && <p className="mt-2 text-center text-xs text-rose-400">{error}</p>}
          <p className="mt-auto pb-3 text-center text-[10px] text-zinc-500">Calling as {name} · {number}</p>
        </>
      ) : (
        <div className="mt-4 flex-1 space-y-1 overflow-y-auto">
          {recents.length === 0 && <p className="mt-10 text-center text-xs text-zinc-500">No recent calls</p>}
          {recents.map((c) => {
            const missed = c.status === "missed" || c.status === "declined";
            const dur = c.answered_at && c.ended_at ? fmtDuration(c.ended_at - c.answered_at) : "";
            return (
              <button key={c.call_id} onClick={onCall} className="flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-zinc-900">
                {missed ? <PhoneMissed className="size-4 text-rose-400" /> : <PhoneIncoming className="size-4 rotate-180 text-emerald-400" />}
                <span className="min-w-0 flex-1">
                  <span className={clsx("block text-sm", missed && "text-rose-300")}>Northbridge Bank</span>
                  <span className="block text-[11px] text-zinc-500">{c.mode === "status" ? "Claim status" : "Fraud report"}{dur && ` · ${dur}`}</span>
                </span>
                <span className="text-[11px] text-zinc-500">{new Date(c.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ───────────────────────────── in call ───────────────────────────── */
function CallScreen({ state, answeredAt, endText, muted, room, call, onMute, onHangup, onMessages, unread }: {
  state: PhoneState; answeredAt: number | null; endText: string; muted: boolean; room: Room | null; call?: Call;
  onMute: () => void; onHangup: () => void; onMessages: () => void; unread: number;
}) {
  const [keypad, setKeypad] = useState(false);
  const [speaker, setSpeaker] = useState(false);
  const [dtmf, setDtmf] = useState("");
  const now = useNow(500);
  if (state === "ended" || state === "idle") {
    return (
      <div className="flex h-full flex-col items-center justify-center bg-gradient-to-b from-zinc-900 to-black">
        <PhoneOff className="size-10 text-rose-400" />
        <p className="mt-4 text-lg font-semibold">{state === "idle" ? "No active call" : endText}</p>
        {answeredAt && <p className="font-mono text-sm text-zinc-400">{fmtDuration(now - answeredAt)}</p>}
      </div>
    );
  }
  const specialist = call?.phase === "interview";
  return (
    <div className="flex h-full flex-col items-center bg-gradient-to-b from-zinc-900 via-zinc-950 to-black px-6 pb-5 pt-4">
      {!keypad ? (
        <>
          <div className="relative mt-2 grid size-20 place-items-center">
            {state === "ringing" && <span className="absolute inset-0 animate-ping rounded-full bg-emerald-500/20" />}
            <div className="grid size-20 place-items-center rounded-full bg-zinc-800 text-xl font-semibold ring-1 ring-zinc-700">NB</div>
          </div>
          <p className="mt-3 text-lg font-semibold">Northbridge Bank</p>
          <p className="font-mono text-xs text-zinc-400">{BANK_NUMBER}</p>
          {state === "ringing"
            ? <p className="mt-1 animate-pulse text-sm text-emerald-400">Ringing…</p>
            : <p className="mt-1 font-mono text-sm text-emerald-400">{fmtDuration(now - (answeredAt ?? now))}</p>}
          {specialist && (
            <p className="mt-2 flex items-center gap-1.5 rounded-full bg-sky-500/15 px-3 py-1 text-xs text-sky-300">
              <UserRound className="size-3.5" /> {call?.specialist?.name ?? "Fraud specialist"} on the line
            </p>
          )}
          {room && (
            <RoomContext.Provider value={room}>
              <StartAudio label="Tap to enable audio" className="mt-2 rounded-full bg-amber-500 px-4 py-1.5 text-sm font-medium text-black" />
              {state === "connected" && !specialist && <LiveCall />}
            </RoomContext.Provider>
          )}
        </>
      ) : (
        <div className="flex flex-1 flex-col items-center">
          <p className="h-8 font-mono text-2xl tracking-widest">{dtmf}</p>
          <div className="mt-3"><Keypad onKey={(k) => setDtmf((s) => (s + k).slice(-12))} /></div>
        </div>
      )}

      <div className="mt-auto w-full">
        <div className="mb-4 grid grid-cols-4 gap-2">
          <CallBtn label={muted ? "Unmute" : "Mute"} active={muted} disabled={state !== "connected"} onClick={onMute}
            icon={muted ? <MicOff className="size-5" /> : <Mic className="size-5" />} />
          <CallBtn label="Keypad" active={keypad} onClick={() => setKeypad(!keypad)} icon={<Grid3x3 className="size-5" />} />
          <CallBtn label="Speaker" active={speaker} onClick={() => setSpeaker(!speaker)} icon={<Volume2 className="size-5" />} />
          <CallBtn label="Messages" onClick={onMessages} badge={unread} icon={<MessageSquare className="size-5" />} />
        </div>
        <div className="flex justify-center">
          <button onClick={onHangup}
            className="grid size-16 place-items-center rounded-full bg-rose-600 shadow-lg shadow-rose-600/30 transition hover:scale-105 hover:bg-rose-500 active:scale-95">
            <PhoneOff className="size-7 text-white" />
          </button>
        </div>
      </div>
    </div>
  );
}

function CallBtn({ label, icon, onClick, active, disabled, badge }: { label: string; icon: React.ReactNode; onClick: () => void; active?: boolean; disabled?: boolean; badge?: number }) {
  return (
    <button onClick={onClick} disabled={disabled} className="flex flex-col items-center gap-1 disabled:opacity-30">
      <span className={clsx("relative grid size-12 place-items-center rounded-full transition", active ? "bg-white text-black" : "bg-zinc-800 text-white hover:bg-zinc-700")}>
        {icon}
        {!!badge && <span className="absolute -right-0.5 -top-0.5 grid min-w-4 place-items-center rounded-full bg-rose-500 px-1 text-[9px] font-bold text-white">{badge}</span>}
      </span>
      <span className="text-[10px] text-zinc-400">{label}</span>
    </button>
  );
}

/** Shown once the AI picks up: voice visualizer + live captions. */
function LiveCall() {
  const { state, audioTrack, agent } = useVoiceAssistant();
  const transcriptions = useTranscriptions();
  const last = transcriptions.slice(-2);
  return (
    <div className="mt-3 flex w-full flex-col items-center">
      <div className="h-12 w-36">
        <BarVisualizer state={state} track={audioTrack} barCount={7} options={{ minHeight: 6 }} />
      </div>
      <p className="mt-1 text-[11px] capitalize text-zinc-500">{agent ? `AI ${state}` : "connecting…"}</p>
      <div className="mt-2 max-h-28 w-full space-y-1 overflow-hidden text-center text-[13px] leading-snug">
        {last.map((t) => (
          <p key={t.streamInfo.id} className={clsx("line-clamp-3", t.participantInfo.identity === agent?.identity ? "text-zinc-200" : "text-emerald-300/80")}>{t.text}</p>
        ))}
      </div>
    </div>
  );
}

/* ───────────────────────────── messages ───────────────────────────── */
function MessagesList({ messages, unread, onOpen }: { messages: Sms[]; unread: number; onOpen: () => void }) {
  const last = messages.at(-1);
  return (
    <div className="flex h-full flex-col px-4 pt-3">
      <h2 className="px-1 text-2xl font-bold">Messages</h2>
      <div className="mt-3 flex-1 overflow-y-auto">
        {!last && <p className="mt-16 text-center text-xs text-zinc-500">No messages yet.<br />Call the bank and a text will arrive here.</p>}
        {last && (
          <button onClick={onOpen} className="flex w-full items-center gap-3 rounded-xl px-2 py-2.5 text-left hover:bg-zinc-900">
            <span className="grid size-11 shrink-0 place-items-center rounded-full bg-indigo-600 text-xs font-bold">NB</span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center justify-between">
                <span className={clsx("text-sm", unread ? "font-bold" : "font-medium")}>{BANK_SENDER}</span>
                <span className="text-[11px] text-zinc-500">{smsTime(last.time)}</span>
              </span>
              <span className={clsx("line-clamp-2 text-xs", unread ? "text-zinc-100" : "text-zinc-400")}>{last.text}</span>
            </span>
            {!!unread && <span className="grid min-w-5 place-items-center rounded-full bg-sky-500 px-1 text-[10px] font-bold">{unread}</span>}
          </button>
        )}
      </div>
    </div>
  );
}

function Thread({ messages }: { messages: Sms[] }) {
  const box = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState<string | null>(null);
  // scroll ONLY the message list (scrollIntoView would also scroll the whole web page away)
  useEffect(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight; }, [messages.length]);
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-zinc-900 px-4 py-2">
        <span className="grid size-8 place-items-center rounded-full bg-indigo-600 text-[10px] font-bold">NB</span>
        <div>
          <p className="text-sm font-semibold">{BANK_SENDER}</p>
          <p className="text-[10px] text-zinc-500">Northbridge Bank · verified sender</p>
        </div>
      </div>
      <div ref={box} className="flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {messages.length === 0 && <p className="mt-10 text-center text-xs text-zinc-500">No messages yet</p>}
        {messages.map((m) => {
          const code = otpOf(m.text);
          return (
            <div key={m.id} className="max-w-[85%]">
              <div className="rounded-2xl rounded-bl-md bg-zinc-800 px-3 py-2 text-[13px] leading-snug text-zinc-100">
                {code && (
                  <div className="mb-1.5 flex items-center justify-between gap-2 rounded-xl bg-zinc-900 px-2.5 py-1.5">
                    <span className="font-mono text-xl font-bold tracking-[0.3em] text-emerald-300">{code}</span>
                    <button onClick={() => { void navigator.clipboard?.writeText(code).catch(() => {}); setCopied(m.id); window.setTimeout(() => setCopied(null), 1500); }}
                      className="flex items-center gap-1 rounded-lg bg-zinc-800 px-2 py-1 text-[10px] text-zinc-300 hover:bg-zinc-700">
                      <Copy className="size-3" /> {copied === m.id ? "Copied" : "Copy"}
                    </button>
                  </div>
                )}
                {m.text}
              </div>
              <p className="ml-2 mt-0.5 text-[10px] text-zinc-500">{smsTime(m.time)}</p>
            </div>
          );
        })}
      </div>
      <div className="border-t border-zinc-900 px-3 py-2">
        <div className="rounded-full bg-zinc-900 px-4 py-2 text-xs text-zinc-600">You can't reply to this sender</div>
      </div>
    </div>
  );
}

/* ───────────────────────────── bank app ───────────────────────────── */
const STEP_LABELS: [keyof Claim["steps"], string][] = [["report", "Reported"], ["interview", "Interview"], ["investigate", "Investigation"], ["followup", "Decision"]];

function BankApp({ number, onCall }: { number: string; onCall: () => void }) {
  const { claimList, callList } = useLive();
  const [cust, setCust] = useState<Customer | null>(null);
  const [txns, setTxns] = useState<Txn[]>([]);
  const myClaims = claimList.filter((c) => digitsOnly(c.phone) === digitsOnly(number));
  const blockKey = callList.reduce((n, c) => n + c.card_actions.length, 0);
  useEffect(() => {
    let alive = true;
    fetch("/api/bank/customers").then((r) => r.json()).then(async (all: Customer[]) => {
      const c = all.find((x) => digitsOnly(x.phone) === digitsOnly(number)) ?? null;
      if (!alive) return;
      setCust(c);
      if (c) { const t = await fetch(`/api/bank/customers/${c.id}/transactions`).then((r) => r.json()); if (alive) setTxns(t); }
    }).catch(() => {});
    return () => { alive = false; };
  }, [number, blockKey, myClaims.length, myClaims.filter((c) => c.refund).length]);
  const disputed = useMemo(() => new Set(myClaims.flatMap((c) => c.disputed_transactions.map((t) => t.id))), [myClaims]);

  return (
    <div className="h-full overflow-y-auto bg-zinc-950 px-4 pb-4 pt-3">
      <div className="flex items-center gap-2">
        <span className="grid size-8 place-items-center rounded-lg bg-indigo-600"><Landmark className="size-4" /></span>
        <div>
          <p className="text-[10px] uppercase tracking-wide text-zinc-500">Northbridge Bank</p>
          <p className="text-sm font-semibold">Hi {cust?.name.split(" ")[0] ?? "there"}</p>
        </div>
      </div>

      {cust?.account && (
        <div className="mt-4 rounded-xl bg-gradient-to-br from-emerald-600/30 to-teal-700/10 p-3 ring-1 ring-emerald-500/30">
          <p className="text-[10px] uppercase tracking-wide text-emerald-200/80">{cust.account.type} · ••{cust.account.last4}</p>
          <p className="mt-1 text-2xl font-semibold tracking-tight">{money(cust.account.balance)}</p>
          <p className="text-[10px] text-zinc-400">Available balance</p>
        </div>
      )}

      <p className="mb-1.5 mt-4 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">My cards</p>
      <div className="space-y-2">
        {(cust?.cards ?? []).map((k) => {
          const blocked = k.status !== "active";
          return (
            <div key={k.last4} className={clsx("rounded-xl p-3 ring-1", blocked ? "bg-rose-500/10 ring-rose-500/30" : "bg-gradient-to-br from-indigo-600/40 to-sky-600/20 ring-indigo-500/30")}>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-xs text-zinc-300"><CreditCard className="size-3.5" /> {k.type}</span>
                <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-semibold", blocked ? "bg-rose-500/20 text-rose-300" : "bg-emerald-500/20 text-emerald-300")}>
                  {blocked ? "Blocked" : "Active"}
                </span>
              </div>
              <p className="mt-2 font-mono text-sm tracking-widest">•••• •••• •••• {k.last4}</p>
            </div>
          );
        })}
      </div>

      <p className="mb-1.5 mt-4 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">My fraud claims</p>
      {myClaims.length === 0 && (
        <button onClick={onCall} className="w-full rounded-xl bg-zinc-900 p-3 text-left text-xs text-zinc-400 ring-1 ring-zinc-800 hover:bg-zinc-800">
          No claims. Spotted a payment you didn't make? <span className="text-emerald-400">Call the fraud helpline →</span>
        </button>
      )}
      {myClaims.map((c) => {
        const decided = c.decision?.outcome;
        const stepDone = (k: keyof Claim["steps"]) => k === "followup" ? !!decided : ["done", "brief_ready", "decided"].includes(c.steps[k]);
        return (
          <div key={c.id} className="mb-2 rounded-xl bg-zinc-900 p-3 ring-1 ring-zinc-800">
            <div className="flex items-center justify-between">
              <p className="font-mono text-xs font-semibold">{c.reference}</p>
              <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-semibold",
                decided === "refund_approved" ? "bg-emerald-500/20 text-emerald-300" : decided === "declined" ? "bg-rose-500/20 text-rose-300" : "bg-amber-500/20 text-amber-300")}>
                {c.refund ? "Refunded" : decided === "refund_approved" ? "Approved" : decided === "declined" ? "Declined" : decided === "more_info" ? "More info needed" : "In progress"}
              </span>
            </div>
            <p className="mt-1 text-sm font-semibold">{money(c.total_disputed, c.currency)}</p>
            {c.refund && <p className="text-[11px] text-emerald-300">Credited to ••{c.refund.account_last4} on {new Date(c.refund.time).toLocaleDateString([], { day: "numeric", month: "short" })}</p>}
            {decided === "declined" && c.decision?.notes && <p className="text-[11px] text-rose-300">Reason: {c.decision.notes}</p>}
            <div className="mt-2 flex items-center">
              {STEP_LABELS.map(([k, label], i) => (
                <div key={k} className="flex flex-1 items-center">
                  <div className="flex flex-col items-center">
                    <span className={clsx("size-2.5 rounded-full", stepDone(k) ? "bg-emerald-400" : c.steps[k] === "in_progress" ? "animate-pulse bg-amber-400" : "bg-zinc-700")} />
                    <span className="mt-1 text-[8px] text-zinc-500">{label}</span>
                  </div>
                  {i < STEP_LABELS.length - 1 && <span className={clsx("mx-0.5 mb-3 h-px flex-1", stepDone(k) ? "bg-emerald-400/60" : "bg-zinc-700")} />}
                </div>
              ))}
            </div>
            <p className="mt-1.5 flex items-center gap-1 text-[10px] text-zinc-500"><Clock3 className="size-3" /> Decision due by {new Date(c.deadline_at).toLocaleDateString([], { day: "numeric", month: "short" })}</p>
          </div>
        );
      })}

      <p className="mb-1.5 mt-4 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">Recent payments</p>
      <div className="divide-y divide-zinc-900 rounded-xl bg-zinc-900/60 ring-1 ring-zinc-800">
        {txns.slice(0, 8).map((t) => (
          <div key={t.id} className="flex items-center justify-between px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-xs">{t.merchant}</p>
              <p className="text-[10px] text-zinc-500">{new Date(t.time).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}{t.card_last4 ? ` · •${t.card_last4}` : " · account credit"}</p>
            </div>
            <div className="shrink-0 whitespace-nowrap pl-2 text-right">
              {t.direction === "credit"
                ? <p className="text-xs font-semibold text-emerald-400">+{money(t.amount, t.currency)}</p>
                : <p className={clsx("text-xs font-medium", t.status === "refunded" && "text-zinc-500 line-through")}>-{money(t.amount, t.currency)}</p>}
              {t.status === "refunded"
                ? <p className="text-[9px] font-semibold text-emerald-300">Refunded</p>
                : disputed.has(t.id) && <p className="text-[9px] font-semibold text-amber-300">Disputed</p>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ───────────────────────────── settings ───────────────────────────── */
function SettingsScreen({ name, number, setName, setNumber, locked }: { name: string; number: string; setName: (s: string) => void; setNumber: (s: string) => void; locked: boolean }) {
  return (
    <div className="h-full px-4 pt-3">
      <h2 className="px-1 text-2xl font-bold">Settings</h2>
      <p className="mb-1.5 mt-4 px-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">This phone (demo customer)</p>
      <div className="space-y-2 rounded-xl bg-zinc-900 p-3 ring-1 ring-zinc-800">
        <label className="block text-[11px] text-zinc-500">Owner name
          <input value={name} disabled={locked} onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-lg bg-zinc-800 px-3 py-1.5 text-sm text-zinc-100 outline-none ring-1 ring-zinc-700 focus:ring-emerald-500 disabled:opacity-50" />
        </label>
        <label className="block text-[11px] text-zinc-500">Mobile number (caller ID)
          <input value={number} disabled={locked} onChange={(e) => setNumber(e.target.value)}
            className="mt-1 w-full rounded-lg bg-zinc-800 px-3 py-1.5 font-mono text-sm text-zinc-100 outline-none ring-1 ring-zinc-700 focus:ring-emerald-500 disabled:opacity-50" />
        </label>
        {locked && <p className="text-[10px] text-amber-300">Can't change during a call.</p>}
      </div>
      <p className="mb-1.5 mt-4 px-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">Demo customers</p>
      <div className="space-y-1.5">
        {[["Amit Sharma", "+91 98765 43210", "shared an OTP with a fake bank caller · ₹45,000"], ["Arjun Mehta", "+91 91234 56789", "senior citizen · fake KYC call, screen-sharing app · ₹60,500"]].map(([n, p, d]) => (
          <button key={p} disabled={locked} onClick={() => { setName(n); setNumber(p); }}
            className={clsx("flex w-full items-center justify-between rounded-xl px-3 py-2 text-left ring-1 disabled:opacity-50",
              digitsOnly(number) === digitsOnly(p) ? "bg-emerald-500/10 ring-emerald-500/30" : "bg-zinc-900 ring-zinc-800 hover:bg-zinc-800")}>
            <span><span className="block text-sm">{n}</span><span className="block text-[10px] text-zinc-500">{d}</span></span>
            <span className="font-mono text-[11px] text-zinc-400">{p}</span>
          </button>
        ))}
      </div>
      <p className="mt-6 text-center text-[10px] text-zinc-600">Simulated phone · synthetic data only</p>
    </div>
  );
}
