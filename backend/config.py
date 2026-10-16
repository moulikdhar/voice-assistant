

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
LLM_MODEL = os.environ.get("LLM_MODEL", "mistral-large-latest")
TTS_MODEL = os.environ.get("TTS_MODEL", "voxtral-mini-tts-latest")
TTS_VOICE = os.environ.get("TTS_VOICE", "gb_jane_neutral")
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "mistral-large-latest")

# App
AGENT_NAME = "claims-intake"
BANK_NAME = os.environ.get("BANK_NAME", "Northbridge Bank")  # fictional bank
AGENT_PERSONA = os.environ.get("AGENT_PERSONA", "Asha")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
RING_SECONDS = float(os.environ.get("RING_SECONDS", "5"))  # auto-answer after N seconds
AMBIENT_SOUND = os.environ.get("AMBIENT_SOUND", "on").lower() == "on"

CALLS_DIR = ROOT_DIR / "calls"
CALLS_DIR.mkdir(exist_ok=True)
