# Backend v2: Mistral SDK 3.3.0 managed voice sessions

Same HTTP API, same `/ws/console` events, same frontend (no frontend changes). The LiveKit Agents framework,
all `livekit-plugins-*`, Silero VAD, the MCP server and the separate agent worker process are gone: the voice
agent runs inside the FastAPI process and the conversation itself (turn-taking, barge-in, Voxtral STT,
Mistral LLM, Voxtral TTS) runs in a Mistral-managed session (`client.audio.sessions.init`).

```
browser phone ── LiveKit room (local `livekit-server --dev`) ── CallAgent (backend process) ── Mistral voice session
                                                                   └─ @function_tool bank tools run here
```

## Run

```powershell
python -m venv .venv-v2
.venv-v2\Scripts\pip install -r backend-v2\requirements.txt
# MISTRAL_API_KEY comes from backend-v2\.env or the project's .env
cd backend-v2
..\.venv-v2\Scripts\python run.py      # also starts ..\tools\livekit-server.exe --dev if it is not running
# second terminal: cd frontend && npm run dev
```

Run v1 **or** v2, not both: they use the same ports (8000, 7880). v2 keeps its own `data/` and `calls/`.

## Why the LiveKit server is still needed

The blueprint's "Option A" (give the browser the `participant_token` from `sessions.init()`) cannot work: a
session has one participant token, and the SDK serves `@function_tool` calls from the participant that joined
with it. If the browser held the token, no bank tool (OTP, card block, claim) could run. "Option B" (browser
WebSocket audio) would need a frontend rewrite. v2 therefore bridges: the backend joins the call's local room
as an agent-kind participant, so the existing LiveKit JS phone works as before.

## File map

| File | vs v1 |
|---|---|
| `db, hub, bank, claims, playbooks, state, prompts, summarizer, assist, brief, llm, winssl` | identical |
| `config.py` | voice-session model overrides (`VOICE_*_MODEL`), no agent/MCP/turn-detection settings |
| `mcp_tools.py` | same tools and logic, as per-call `@function_tool` closures (no MCP server) |
| `agent.py` | rewritten: room ↔ Mistral session bridge, interview transcription, recorder, latency |
| `server.py` | in-process agent instead of LiveKit dispatch; no `/mcp`; `handle_event()` replaces the HTTP hop |
| `sim_specialist.py` | identical except token minting |
| `lk.py` | new: LiveKit JWTs + DeleteRoom (replaces the `livekit-api` package) |
| `voxtral_stt.py` | deleted |

## Differences from v1

Missing or weaker (limits of the managed session, not bugs):

- **Speech cleaning before TTS**: v1 removed `*stage directions*`/markdown before Voxtral spoke; v2 can only clean transcripts. The prompt still forbids markdown.
- **Spoken recovery lines**: v1 said "the line broke up, could you say that again?" on STT errors and "small technical problem" on LLM errors, and had its own STT stall watchdog. v2 only shows a console warning; Mistral handles recovery.
- **OTP timing**: the SMS goes out when the AI answers (during the greeting), not after the caller's first answer. The "first reply" task is in the instructions, because the session can't inject a message into one specific turn.
- **Greeting can be interrupted** (v1: `allow_interruptions=False` for the greeting).
- **Silence check-ins** are two fixed messages (v1 generated "repeat your last question"). What Mistral does after the last reminder is not documented.
- **`interrupted` flag** on AI lines is never set ("· interrupted" never shows in the console).
- **LLM `temperature=0.3` / `max_tool_steps=6`** can't be set (`AudioLLMConfig` takes only `model`).
- **Cost estimate**: LLM tokens are not reported (LLM cost = 0); STT seconds ≈ call duration; TTS characters come from the transcript.
- **Latency per turn** is read from the session snapshot about 2 s after each AI reply (v1: instantly, from metrics events).
- **Interview transcription** uses a simple energy gate instead of Silero VAD; quiet or noisy audio may segment worse. Dropped or stalled Voxtral streams reconnect and re-send the untranscribed audio (as v1's watchdog did).
- **Turn-taking** is Mistral's: a long pause after "No," can end the caller's turn early, so the AI starts, gets interrupted, and the turn shows as two lines.
- **Phone captions** arrive per finished sentence (v1 streamed words as they were spoken).
- **`user_state` "away"** is no longer reported (only speaking/listening, inferred from transcripts).
- **Removed**: ambient office/typing sounds (`AMBIENT_SOUND`), the `TURN_DETECTION` choice, and the `/mcp/` endpoint (external MCP clients can no longer call the bank tools).
