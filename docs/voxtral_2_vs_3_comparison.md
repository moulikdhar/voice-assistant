# Comprehensive Specification & Capability Comparison: Voxtral 2 vs. Voxtral 3

This document provides an exhaustive, specification-level comparison between **Voxtral Transcribe 2** (released February 4, 2026) and **Voxtral 3** (released October 20, 2026), based directly on Mistral's official architectural announcements, benchmark disclosures, and SDK implementations.

---

## 1. Executive Summary & Core Evolution

| Dimension | Voxtral 2 (Feb 2026) | Voxtral 3 (Oct 2026) | Evolution / Impact |
|:---|:---|:---|:---|
| **Scope of Family** | **Pure Speech-to-Text (STT)** only | **Full Audio Intelligence Stack** (STT + TTS + Voice Agent Pipeline) | Expanded from a transcription tool to an interactive end-to-end voice platform |
| **Model Modalities** | Audio-in $\rightarrow$ Text-out | Audio-in $\rightarrow$ Text-out **AND** Text-in $\rightarrow$ Audio-out **AND** Live Voice Agent Sessions | Full duplex multi-modal audio ecosystem |
| **Language Coverage** | **13 languages** | **50+ languages** (STT), **35+ languages & 50+ dialects** (TTS) | +37 languages; covers all 24 EU languages + low-resource Indic/Asian languages |
| **Diarization Performance** | ~30.9% cpWER (TalkBank & English benchmarks) | **18.4% cpWER** (Voxtral Small 3) / **18.6%** (Voxtral Mini 3) | **40% reduction in diarization errors**, beating Pyannote Precision 3 and Scribe v2 |
| **Context Biasing** | Up to 100 terms, optimized for English only | Multilingual keyphrase & free-form context biasing | **94.6% keyphrase recall** on Earnings-22; works across technical & multilingual terms |
| **Turn Detection** | Downstream client/VAD silence thresholds | **Native acoustic + semantic End-of-Turn (EoT) prediction** | Distinguishes natural mid-sentence pauses from true turn completion; eliminates cutoffs |
| **Text-to-Speech (TTS)** | *None* | `Voxtral Mini TTS 3` & `Voxtral Speech 3` with 34 inline emotions & pace controls | Full expressive voice synthesis with phoneme pronunciation control & voice cloning |
| **Voice Agent SDK** | *None* (manual plumbing required) | Native LiveKit WebRTC streaming engine (`mistralai.extra.voice_agents`) | Sub-second (<1s) conversational turns, native barge-in, SIP telephony & custom tools |
| **Open Weights** | Voxtral Realtime V2 (Apache 2.0 on Hugging Face) | Sovereign API (EU, US, Global endpoints) & on-prem self-deployment | Enterprise-grade private cloud and on-premise deployment options |

---

## 2. Model Lineup & Parameter Breakdown

```
VOXTRAL 2 FAMILY (Feb 2026)
├── Voxtral Mini Transcribe V2 (4B params) ── Batch STT ($0.003/min)
└── Voxtral Realtime (4B params, Apache 2.0) ── Streaming STT ($0.006/min)

VOXTRAL 3 FAMILY (Oct 2026)
├── SPEECH-TO-TEXT (STT)
│   ├── Voxtral Mini Transcribe 3 (4B params) ── Batch STT ($0.0035/min)
│   ├── Voxtral Small Transcribe 3 (Flagship) ── High-Accuracy Batch STT ($0.0040/min)
│   └── Voxtral Mini Transcribe Realtime 3 ── Low-latency Streaming STT ($0.0080/min)
├── TEXT-TO-SPEECH (TTS)
│   ├── Voxtral Mini TTS 3 / Voxtral Speech 3 ── Expressive Speech Synthesis ($13/1M chars)
│   └── Voxtral Pro Voice ── High-fidelity Voice Adaptation (from 30m audio)
└── VOICE AGENT SDK (LiveKit Orchestration)
    ├── Managed Voice Agent API + Mistral Small 4 ($0.04/min)
    └── Managed Voice Agent API + Large Model ($0.08/min)
```

---

## 3. Deep-Dive Specification Comparison

### A. Speech-to-Text (STT) Specifications

| Feature / Spec | Voxtral Transcribe 2 | Voxtral 3 | Technical Difference & Advantage |
|:---|:---|:---|:---|
| **Supported Languages** | 13 languages:<br>`EN, ZH, HI, ES, AR, FR, PT, RU, DE, JA, KO, IT, NL` | **50+ languages** (STT):<br>All 24 official EU languages + Indic (`HI, BN, GU, KN, ML, MR, TE, TA`) + Asian (`JA, KO, ZH, TH, VI, FIL`), etc. | 4x language expansion with native dialect support |
| **Batch Transcription Models** | `Voxtral Mini Transcribe V2` | `Voxtral Mini Transcribe 3` (4B)<br>`Voxtral Small Transcribe 3` (Large flagship) | Voxtral 3 adds a larger flagship tier for ultra-low WER in complex acoustic environments |
| **Streaming Latency** | Configurable: 200ms – 2400ms | Sub-500ms (operational target down to 240ms) | Enhanced temporal streaming stability with lower word jitter |
| **FLEURS Multilingual WER** | ~4.0% (across top 10 languages) | **4.1% Forced / 4.0% Inferred** (across **45 languages**) | Voxtral 3 maintains ~4% WER across 45 languages, beating Scribe v2 (6.3%) & Whisper Large-v3 (16.0%) |
| **Diarization Error Rate (cpWER)** | 30.9% cpWER | **18.4% cpWER** (Voxtral Small 3)<br>**18.6% cpWER** (Voxtral Mini 3) | **~40% relative error reduction**; resolves multi-speaker overlap significantly better |
| **Context Biasing / Vocabulary** | Up to 100 words/phrases; optimized for English | Free-form text and domain keyword biasing across all languages | **94.6% keyphrase recall** on Earnings-22 (vs 81.4% without context); adapts without fine-tuning |
| **End-of-Turn (EoT) Detection** | External silence thresholding | **Native acoustic + semantic multimodal prediction** | Model detects linguistic completion and vocal cadence, drastically cutting false interruptions |
| **Audio File Constraints** | Up to 3 hours / 1 GB per file | Up to 3 hours / 1 GB per file | Same maximum file capacity, but 3x faster processing throughput |
| **Audio Formats Supported** | `.mp3, .wav, .m4a, .flac, .ogg` | `.mp3, .wav, .m4a, .flac, .ogg, .pcm, .opus` | Added raw PCM and Opus support for VoIP and WebRTC streaming |

---

### B. Text-to-Speech (TTS) & Voice Generation

| Feature / Spec | Voxtral Transcribe 2 | Voxtral 3 |
|:---|:---|:---|
| **TTS Model Presence** | **None** (No TTS in V2) | `Voxtral Mini TTS 3` (`voxtral-mini-tts-3-0`) & `Voxtral Speech 3` |
| **Realtime TTS** | None | `voxtral-mini-tts-realtime-3-0` (chunked streaming synthesis) |
| **Language & Dialect Support** | N/A | **35+ languages, 50+ regional dialects** (e.g., US/UK English, France/Quebec French, Spain/Latin America Spanish) |
| **Preset Voices** | N/A | Available across 18 languages out of the box |
| **Emotion & Expressiveness Control** | N/A | **34 inline emotional tags** (excited, angry, empathetic, calm, etc.) + speed tags (`slow`, `fast`) |
| **Pronunciation Control** | N/A | Custom pronunciation using International Phonetic Alphabet (IPA) phonemes |
| **Voice Cloning** | N/A | **Zero-shot cloning** (instant) + **Pro Voice** (30-minute studio audio for full stylistic reproduction) |
| **Output Formats** | N/A | `mp3, wav, pcm (24kHz/48kHz), flac, opus` |

---

### C. Conversational Voice Agent Pipeline & Architecture

| Feature / Spec | Voxtral Transcribe 2 | Voxtral 3 |
|:---|:---|:---|
| **Voice Agent Framework** | None (user had to manually build WebSocket pipelines) | **Native Voice Agent SDK** (`mistralai.extra.voice_agents`) |
| **Transport Protocol** | Raw WebSockets (STT only) | **LiveKit WebRTC** (rust core via `livekit-rtc`) |
| **End-to-End Latency** | N/A (>2000ms if chained externally) | **Sub-second (<1,000ms)** conversational turn-around |
| **Barge-In (Interruption Handling)** | N/A | **Zero-latency server-side barge-in**: client continuously pumps mic audio; server cancels in-flight TTS immediately |
| **Telephony Integration** | None | Native **SIP & WebRTC** support with built-in `HangUpTool`, `TransferCallTool`, `PhoneDestination`, and `SipDestination` |
| **Built-in Tool Providers** | None | `WebSearchTool`, `DocumentLibraryTool`, `CustomConnector` (CRM/ERP), and decorated Python `FunctionTool` |
| **Tool Call Fillers** | None | Native `StaticToolFiller` (plays short contextual filler speech during background tool execution) |

---

## 4. Benchmark Comparison Data

### Multilingual Word Error Rate (FLEURS Benchmark)
*Lower Word Error Rate (WER) is better.*

```
Whisper Large-v3              | ████████████████ 16.0%
GPT Transcribe                 | ██████ 6.3%
Scribe v2 (ElevenLabs)        | █████ 4.9%
Gemini 3.5 Transcribe         | ████ 4.6%
Voxtral Mini Transcribe V2    | ████ 4.1% (13 languages)
Voxtral Mini Transcribe 3     | ████ 4.1% (45 languages)
Voxtral Small Transcribe 3    | ████ 4.0% (45 languages - State of the Art)
```

### Diarization Accuracy (cpWER on AMI, Switchboard, CallHome, AISHELL-4)
*Concatenated Minimum Permutation Word Error Rate — Lower is better.*

```
Gemini 3.5 Transcribe         | ████████████████████ 38.4%
Voxtral Mini Transcribe V2    | ███████████████ 30.9%
Scribe v2                     | ███████████ 23.0%
Microsoft AI Transcribe 2     | ██████████ 20.3%
Pyannote Precision 3          | █████████ 19.0%
Voxtral Mini Transcribe 3     | █████████ 18.6%
Voxtral Small Transcribe 3    | █████████ 18.4% (Industry Leading)
```

### Keyphrase Recall with Context Biasing (Earnings-22 Benchmark)
*Higher Recall percentage is better.*

| Model | Without Context Prompting | With Context Prompting | Uplift |
|:---|:---:|:---:|:---:|
| **Voxtral Small Transcribe 3** | **81.4%** | **94.6%** | **+13.2%** |
| Scribe v2 | 84.0% | 94.3% | +10.3% |
| Gemini 3.5 Transcribe | 80.9% | 87.3% | +6.4% |
| Voxtral Mini Transcribe V2 | 78.0% | 89.2% | +11.2% |

---

## 5. Pricing & Economics Comparison

| Model / Service | Voxtral 2 Pricing | Voxtral 3 Pricing | Value Change |
|:---|:---|:---|:---|
| **Batch Mini STT** | **$0.0030 / min** | **$0.0035 / min** | +$0.0005/min (+16%) for 4x more languages & 40% better diarization |
| **Flagship Batch STT** | *N/A* | **$0.0040 / min** | SOTA high-accuracy transcription tier |
| **Realtime Streaming STT** | **$0.0060 / min** | **$0.0080 / min** | Realtime sub-500ms streaming with acoustic+semantic turn detection |
| **TTS (Speech Generation)** | *N/A* | **$13.00 / 1M characters** | Significantly lower than ElevenLabs ($30–$80/1M chars) and Cartesia ($40/1M chars) |
| **Full Voice Agent API** | *N/A* | **$0.0400 / min** (Small 4)<br>**$0.0800 / min** (Large) | Complete turn-key pipeline (STT + LLM + TTS + WebRTC) |
| **Open Source Weights** | Voxtral Realtime (Apache 2.0) | API + On-Premise Sovereign Self-Hosting | Enterprise containerized self-deployment |

---

## 6. Migration Guide: From Voxtral 2 to Voxtral 3

If you have existing code using Voxtral 2 endpoints, here is how the models and namespaces map:

### Model Identifier Mapping

| Purpose | Voxtral 2 Model ID | Voxtral 3 Replacement Model ID |
|:---|:---|:---|
| Batch Lightweight | `voxtral-mini-2602` / `voxtral-mini-latest` | `voxtral-mini-transcribe-3` (`voxtral-mini-transcribe-3-0`) |
| Batch High-Accuracy | *N/A* | `voxtral-small-transcribe-3` (`voxtral-small-transcribe-3-0`) |
| Realtime Streaming | `voxtral-mini-transcribe-realtime-2602` | `voxtral-mini-transcribe-realtime-3` (`voxtral-mini-realtime-latest`) |
| Text-to-Speech | *N/A* | `voxtral-mini-tts-3` (`voxtral-mini-tts-latest`) |

### Code Migration Example

#### Voxtral 2 (Batch STT only):
```python
from mistralai.client import Mistral

client = Mistral()
with open("meeting.mp3", "rb") as f:
    res = client.audio.transcriptions.complete(
        model="voxtral-mini-latest",  # Voxtral 2
        file=f,
        diarize=True,
    )
print(res.text)
```

#### Voxtral 3 (Modern Flagship STT + Context Biasing):
```python
from mistralai.client import Mistral

client = Mistral()
with open("meeting.mp3", "rb") as f:
    res = client.audio.transcriptions.complete(
        model="voxtral-small-transcribe-3",  # Voxtral 3 Flagship
        file=f,
        diarize=True,
        context_bias=["Q3 Earnings", "EBITDA", "Mistral AI", "Kubernetes"],
        language="en",
    )
for segment in res.segments:
    print(f"[{segment.speaker}] {segment.start}s - {segment.end}s: {segment.text}")
```
