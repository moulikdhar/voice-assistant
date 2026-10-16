// "Customer Phone": a phone-shaped screen. You dial the bank, hear it ring, talk to the AI.
import { useEffect, useRef, useState } from "react";
import {
  ParticipantKind,
  Room,
  RoomEvent,
  type RemoteParticipant,
} from "livekit-client";
import {
  BarVisualizer,
  RoomAudioRenderer,
  RoomContext,
  StartAudio,
  useTranscriptions,
  useVoiceAssistant,
} from "@livekit/components-react";
import { motion, AnimatePresence } from "motion/react";
import { Mic, MicOff, Phone as PhoneIcon, PhoneOff, Pencil, ShieldCheck } from "lucide-react";
import clsx from "clsx";
import { api, fmtDuration } from "../lib/api";
import { endTone, startRingback, unlockAudio } from "../lib/tones";
import { useNow } from "../lib/useConsole";

const BANK_NUMBER = "1800 266 0101";

type PhoneState = "idle" | "ringing" | "connected" | "ended";

export default function Phone() {
  const [state, setState] = useState<PhoneState>("idle");
  const [room, setRoom] = useState<Room | null>(null);
  const [callId, setCallId] = useState<string | null>(null);
  const [answeredAt, setAnsweredAt] = useState<number | null>(null);
  const [muted, setMuted] = useState(false);
  const [endText, setEndText] = useState("Call ended");
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("Riya Sharma");
  const [number, setNumber] = useState("+91 98765 43210");
  const [editing, setEditing] = useState(false);
  const stopRing = useRef<(() => void) | null>(null);
  const endedRef = useRef(false);
  // keep a ref so the Disconnected handler knows whether the call was answered
  const answeredRef = useRef(false);
  useEffect(() => {
    answeredRef.current = answeredAt !== null;
  }, [answeredAt]);

  const finish = (text: string) => {
    if (endedRef.current) return;
    endedRef.current = true;
    stopRing.current?.();
    stopRing.current = null;
    endTone();
    setEndText(text);
    setState("ended");
    window.setTimeout(() => {
      setState("idle");
      setRoom(null);
      setCallId(null);
      setAnsweredAt(null);
      setMuted(false);
    }, 2500);
  };

  async function dial() {
    unlockAudio(); // the click allows the browser to play sound
    setError(null);
    endedRef.current = false;
    setState("ringing");
    stopRing.current = startRingback();
    try {
      const { call, livekit_url, token } = await api.startCall(name, number);
      setCallId(call.call_id);

      const r = new Room({ adaptiveStream: true });
      const onAgent = (p: RemoteParticipant) => {
        if (p.kind !== ParticipantKind.AGENT) return;
        stopRing.current?.();
        stopRing.current = null;
        answeredRef.current = true;
        setAnsweredAt(Date.now() / 1000);
        setState("connected");
      };
      r.on(RoomEvent.ParticipantConnected, onAgent);
      r.on(RoomEvent.ParticipantDisconnected, (p) => {
        if (p.kind === ParticipantKind.AGENT) {
          finish("Call ended");
          void r.disconnect();
        }
      });
      // Console declined / ended the call -> the backend deletes the room -> we are disconnected
      r.on(RoomEvent.Disconnected, () => finish(answeredRef.current ? "Call ended" : "Call not answered"));

      await r.connect(livekit_url, token);
      await r.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
      r.remoteParticipants.forEach(onAgent);
      setRoom(r);
    } catch (e) {
      stopRing.current?.();
      setError(e instanceof Error ? e.message : String(e));
      setState("idle");
    }
  }

  async function hangup() {
    const id = callId;
    finish("Call ended");
    if (id) void api.hangup(id).catch(() => {});
    await room?.disconnect();
  }

  async function toggleMute() {
    if (!room) return;
    await room.localParticipant.setMicrophoneEnabled(muted);
    setMuted(!muted);
  }

  useEffect(() => () => void room?.disconnect(), [room]);

  return (
    <div className="mx-auto w-[340px] shrink-0">
      {/* phone frame */}
      <div className="relative h-[680px] rounded-[3rem] border-[10px] border-zinc-800 bg-gradient-to-b from-zinc-900 via-zinc-950 to-black shadow-2xl shadow-black/60 ring-1 ring-zinc-700">
        <div className="absolute left-1/2 top-2 h-6 w-28 -translate-x-1/2 rounded-full bg-black" />
        <div className="flex h-full flex-col px-6 pb-8 pt-14">
          <AnimatePresence mode="wait">
            {state === "idle" && (
              <motion.div
                key="idle"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex flex-1 flex-col items-center"
              >
                <ShieldCheck className="mt-6 size-12 text-emerald-400" />
                <p className="mt-4 text-sm text-zinc-400">Northbridge Bank</p>
                <p className="text-lg font-semibold">Fraud & Disputes Helpline</p>
                <p className="mt-6 font-mono text-3xl tracking-wider">{BANK_NUMBER}</p>
                <p className="mt-1 text-xs text-zinc-500">24 × 7 · AI assisted</p>

                <div className="mt-10 w-full rounded-2xl bg-zinc-900/80 p-4 text-sm ring-1 ring-zinc-800">
                  <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wide text-zinc-500">
                    Calling as
                    <button onClick={() => setEditing(!editing)} className="text-zinc-400 hover:text-white">
                      <Pencil className="size-3.5" />
                    </button>
                  </div>
                  {editing ? (
                    <div className="space-y-2">
                      <input value={name} onChange={(e) => setName(e.target.value)}
                        className="w-full rounded-lg bg-zinc-800 px-3 py-1.5 outline-none ring-1 ring-zinc-700 focus:ring-emerald-500" />
                      <input value={number} onChange={(e) => setNumber(e.target.value)}
                        className="w-full rounded-lg bg-zinc-800 px-3 py-1.5 font-mono outline-none ring-1 ring-zinc-700 focus:ring-emerald-500" />
                    </div>
                  ) : (
                    <>
                      <p className="font-medium">{name}</p>
                      <p className="font-mono text-zinc-400">{number}</p>
                    </>
                  )}
                </div>
                {error && <p className="mt-3 text-center text-xs text-rose-400">{error}</p>}

                <button onClick={dial}
                  className="mt-auto grid size-20 place-items-center rounded-full bg-emerald-500 shadow-lg shadow-emerald-500/30 transition hover:scale-105 hover:bg-emerald-400 active:scale-95">
                  <PhoneIcon className="size-8 fill-white text-white" />
                </button>
              </motion.div>
            )}

            {(state === "ringing" || state === "connected") && (
              <motion.div key="call" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                className="flex flex-1 flex-col items-center">
                <div className="relative mt-6 grid size-24 place-items-center">
                  {state === "ringing" && (
                    <>
                      <span className="absolute inset-0 animate-ping rounded-full bg-emerald-500/20" />
                      <span className="absolute inset-2 animate-pulse rounded-full bg-emerald-500/10" />
                    </>
                  )}
                  <div className="grid size-24 place-items-center rounded-full bg-zinc-800 text-2xl font-semibold ring-1 ring-zinc-700">
                    NB
                  </div>
                </div>
                <p className="mt-5 text-lg font-semibold">Northbridge Bank</p>
                <p className="font-mono text-sm text-zinc-400">{BANK_NUMBER}</p>
                <CallTimer state={state} answeredAt={answeredAt} />

                {room && (
                  <RoomContext.Provider value={room}>
                    <RoomAudioRenderer />
                    <StartAudio label="Tap to enable audio"
                      className="mt-3 rounded-full bg-amber-500 px-4 py-1.5 text-sm font-medium text-black" />
                    {state === "connected" && <LiveCall />}
                  </RoomContext.Provider>
                )}

                <div className="mt-auto flex items-center gap-8">
                  <button onClick={toggleMute} disabled={state !== "connected"}
                    className={clsx("grid size-14 place-items-center rounded-full transition disabled:opacity-30",
                      muted ? "bg-white text-black" : "bg-zinc-800 text-white hover:bg-zinc-700")}>
                    {muted ? <MicOff className="size-6" /> : <Mic className="size-6" />}
                  </button>
                  <button onClick={hangup}
                    className="grid size-20 place-items-center rounded-full bg-rose-600 shadow-lg shadow-rose-600/30 transition hover:scale-105 hover:bg-rose-500 active:scale-95">
                    <PhoneOff className="size-8 text-white" />
                  </button>
                  <div className="size-14" />
                </div>
              </motion.div>
            )}

            {state === "ended" && (
              <motion.div key="ended" initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0 }} className="flex flex-1 flex-col items-center justify-center">
                <PhoneOff className="size-10 text-rose-400" />
                <p className="mt-4 text-lg font-semibold">{endText}</p>
                {answeredAt && (
                  <p className="font-mono text-sm text-zinc-400">{fmtDuration(Date.now() / 1000 - answeredAt)}</p>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
      <p className="mt-3 text-center text-xs text-zinc-500">Customer phone (simulated) · use headphones</p>
    </div>
  );
}

function CallTimer({ state, answeredAt }: { state: PhoneState; answeredAt: number | null }) {
  const now = useNow(500);
  if (state === "ringing") return <p className="mt-2 animate-pulse text-sm text-emerald-400">Ringing…</p>;
  return <p className="mt-2 font-mono text-sm text-emerald-400">{fmtDuration(now - (answeredAt ?? now))}</p>;
}

/** Shown once the AI picks up: voice visualizer + live captions. */
function LiveCall() {
  const { state, audioTrack, agent } = useVoiceAssistant();
  const transcriptions = useTranscriptions();
  const last = transcriptions.slice(-2);
  return (
    <div className="mt-6 flex w-full flex-col items-center">
      <div className="h-16 w-40">
        <BarVisualizer state={state} track={audioTrack} barCount={7} options={{ minHeight: 8 }} />
      </div>
      <p className="mt-2 text-xs capitalize text-zinc-500">{agent ? `AI ${state}` : "connecting…"}</p>
      <div className="mt-4 min-h-24 w-full space-y-1.5 text-center text-sm leading-snug">
        {last.map((t) => {
          const isAgent = t.participantInfo.identity === agent?.identity;
          return (
            <p key={t.streamInfo.id} className={isAgent ? "text-zinc-200" : "text-emerald-300/80"}>
              {t.text}
            </p>
          );
        })}
      </div>
    </div>
  );
}
