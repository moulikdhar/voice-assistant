// Phone sounds generated with the Web Audio API (no audio files needed).
//  - ringback: the "tring-tring" you hear while the other side is ringing (India/UK cadence)
//  - endTone:  three short beeps when the call ends

let ctx: AudioContext | null = null;
function audio(): AudioContext {
  ctx ??= new AudioContext();
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

function burst(ac: AudioContext, out: GainNode, freqs: number[], start: number, dur: number) {
  for (const f of freqs) {
    const osc = ac.createOscillator();
    const g = ac.createGain();
    osc.frequency.value = f;
    g.gain.setValueAtTime(0, start);
    g.gain.linearRampToValueAtTime(0.12, start + 0.02);
    g.gain.setValueAtTime(0.12, start + dur - 0.02);
    g.gain.linearRampToValueAtTime(0, start + dur);
    osc.connect(g).connect(out);
    osc.start(start);
    osc.stop(start + dur + 0.05);
  }
}

/** Starts the ringback loop. Returns a function that stops it. */
export function startRingback(): () => void {
  const ac = audio();
  const out = ac.createGain();
  out.connect(ac.destination);
  let stopped = false;
  const cycle = () => {
    if (stopped) return;
    const t = ac.currentTime + 0.05;
    burst(ac, out, [400, 450], t, 0.4); // tring
    burst(ac, out, [400, 450], t + 0.6, 0.4); // tring
  };
  cycle();
  const timer = window.setInterval(cycle, 3000);
  return () => {
    stopped = true;
    window.clearInterval(timer);
    out.gain.setValueAtTime(0, ac.currentTime);
    window.setTimeout(() => out.disconnect(), 200);
  };
}

export function endTone(): void {
  const ac = audio();
  const out = ac.createGain();
  out.connect(ac.destination);
  const t = ac.currentTime + 0.05;
  for (let i = 0; i < 3; i++) burst(ac, out, [425], t + i * 0.35, 0.18);
}

/** Must be called from a click handler once, so browsers allow sound. */
export function unlockAudio(): void {
  audio();
}
