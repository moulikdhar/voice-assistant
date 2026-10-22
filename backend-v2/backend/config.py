from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent  # backend-v2/
load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR.parent / ".env")  # fall back to the project's .env (same MISTRAL_API_KEY as backend v1)

# LiveKit server = only the browser's "phone line" now (`livekit-server --dev`). There is no agent worker:
# the backend itself joins the room and bridges the audio to a Mistral-managed voice session.
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")

# Mistral
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")

# Managed voice session (client.audio.sessions.init). Empty = Mistral's default model for that stage.
VOICE_LLM_MODEL = os.environ.get("VOICE_LLM_MODEL", "mistral-large-latest")  # same brain as v1
VOICE_STT_MODEL = os.environ.get("VOICE_STT_MODEL", "")
VOICE_TTS_MODEL = os.environ.get("VOICE_TTS_MODEL", "")

# Direct API calls (interview transcription, simulated specialist voice, summaries, batch transcript)
STT_MODEL = os.environ.get("STT_MODEL", "voxtral-mini-transcribe-realtime-2602")  # specialist interview channels
STT_DELAY_MS = int(os.environ.get("STT_DELAY_MS", "480"))  # lower = faster, less accurate
BATCH_STT_MODEL = os.environ.get("BATCH_STT_MODEL", "voxtral-mini-latest")  # Transcribe V2 (diarisation)
LLM_MODEL = os.environ.get("LLM_MODEL", "mistral-large-latest")
TTS_MODEL = os.environ.get("TTS_MODEL", "voxtral-mini-tts-latest")
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "mistral-large-latest")
# Voices (Voxtral TTS voice ids). Change them in .env, no code change needed.
#   AGENT_VOICE      = the AI assistant (AGENT_PERSONA, default "Asha")
#   SPECIALIST_VOICE = the simulated human fraud specialist (SPECIALIST_NAME, default "Oliver")
AGENT_VOICE = os.environ.get("AGENT_VOICE") or os.environ.get("TTS_VOICE") or "gb_jane_neutral"
TTS_VOICE = AGENT_VOICE  # old name, kept so older .env files still work
SPECIALIST_VOICE = os.environ.get("SPECIALIST_VOICE", "gb_oliver_confident")

# App
AGENT_NAME = "claims-intake"
BANK_NAME = os.environ.get("BANK_NAME", "Northbridge Bank")  # fictional bank
AGENT_PERSONA = os.environ.get("AGENT_PERSONA", "Asha")
SPECIALIST_NAME = os.environ.get("SPECIALIST_NAME", "Oliver")
JURISDICTION = os.environ.get("JURISDICTION", "IN_RBI")  # IN_RBI | US_REG_E | UK_PSR
RING_SECONDS = float(os.environ.get("RING_SECONDS", "5"))  # auto-answer after N seconds
SILENCE_CHECK_SECONDS = float(os.environ.get("SILENCE_CHECK_SECONDS", "10"))  # caller silent -> gentle check-in
RECORD_CALLS = os.environ.get("RECORD_CALLS", "on").lower() == "on"

CALLS_DIR = ROOT_DIR / "calls"
CALLS_DIR.mkdir(exist_ok=True)
AUDIO_DIR = CALLS_DIR / "audio"
AUDIO_DIR.mkdir(exist_ok=True)
