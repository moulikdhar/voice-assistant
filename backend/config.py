
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# LiveKit (local dev server defaults: `livekit-server --dev`)
os.environ.setdefault("LIVEKIT_URL", "ws://localhost:7880")
os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]

# Mistral
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
STT_MODEL = os.environ.get("STT_MODEL", "voxtral-mini-transcribe-realtime-2602")
STT_DELAY_MS = int(os.environ.get("STT_DELAY_MS", "480"))  # lower = faster, less accurate
BATCH_STT_MODEL = os.environ.get("BATCH_STT_MODEL", "voxtral-mini-latest")  # Transcribe V2
LLM_MODEL = os.environ.get("LLM_MODEL", "mistral-large-latest")
TTS_MODEL = os.environ.get("TTS_MODEL", "voxtral-mini-tts-latest")
# Voices (Voxtral TTS voice ids). Change them in .env, no code change needed.
#   AGENT_VOICE      = the AI assistant (AGENT_PERSONA, default "Asha")
#   SPECIALIST_VOICE = the simulated human fraud specialist (SPECIALIST_NAME, default "Oliver")
AGENT_VOICE = os.environ.get("AGENT_VOICE") or os.environ.get("TTS_VOICE") or "gb_jane_neutral"
TTS_VOICE = AGENT_VOICE  # old name, kept so older .env files still work
SPECIALIST_VOICE = os.environ.get("SPECIALIST_VOICE", "gb_oliver_confident")
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "mistral-large-latest")

# App
AGENT_NAME = "claims-intake"
BANK_NAME = os.environ.get("BANK_NAME", "Northbridge Bank")  # fictional bank
AGENT_PERSONA = os.environ.get("AGENT_PERSONA", "Asha")
SPECIALIST_NAME = os.environ.get("SPECIALIST_NAME", "Oliver")
# End-of-turn detection (when has the caller finished speaking?):
#   local = LiveKit's small turn-detector model on this machine (default; no LiveKit Cloud attempt first)
#   vad   = silence only (lightest on CPU; use it on a slow laptop)
#   stt   = end-of-speech from the transcription model
TURN_DETECTION = os.environ.get("TURN_DETECTION", "local").lower()
JURISDICTION = os.environ.get("JURISDICTION", "IN_RBI")  # IN_RBI | US_REG_E | UK_PSR
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
MCP_URL = os.environ.get("MCP_URL", f"{BACKEND_URL}/mcp/")
RING_SECONDS = float(os.environ.get("RING_SECONDS", "5"))  # auto-answer after N seconds
AMBIENT_SOUND = os.environ.get("AMBIENT_SOUND", "off").lower() == "on"
RECORD_CALLS = os.environ.get("RECORD_CALLS", "on").lower() == "on"

CALLS_DIR = ROOT_DIR / "calls"
CALLS_DIR.mkdir(exist_ok=True)
AUDIO_DIR = CALLS_DIR / "audio"
AUDIO_DIR.mkdir(exist_ok=True)
