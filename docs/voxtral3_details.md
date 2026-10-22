# Deep Dive: 5 Core Capabilities of the Mistral Python SDK (v3.3.0)

> **Source of truth**: All code excerpts are pulled directly from the installed package at  
> `.venv/Lib/site-packages/mistralai/` — not from documentation.

---

## 1. LiveKit WebRTC — Full Detail

### What It Is

LiveKit is an open-source **WebRTC server and client SDK** (Rust core, exposed to Python via FFI bindings in `livekit-rtc`). The Mistral SDK uses it exclusively for the voice agent pipeline. It handles:

- Establishing WebRTC peer connections between the Python client and the Mistral cloud audio worker.
- Publishing and subscribing to audio tracks in real-time.
- Sending bidirectional **RPC data messages** (tool calls) between the client and agent worker.

### Where It Lives in the SDK

The file `mistralai/extra/voice_agents/transport.py` is the complete implementation of the LiveKit integration. The key class is `LiveKitTransport`.

```python
# transport.py — line 12-21: hard dependency on livekit-rtc
try:
    import numpy as np
    from livekit import rtc
    from livekit.rtc.room import EventTypes
except ModuleNotFoundError as exc:
    raise VoiceAgentDependencyError(
        "Voice agents require: pip install 'mistralai[voice-agents]'"
    ) from exc
```

### Session Lifecycle: What Happens Under the Hood

**Step 1: Create Remote Session (REST)**

```python
# client.audio.sessions.init() → POST /v1/audio/sessions
# Returns:
resp = AudioSessionCreateResponse(
    id="sess_xyz",
    participant_token="eyJ...",  # LiveKit JWT (30s expiry window)
    server_url="wss://livekit.mistral.ai",
)
```

**Step 2: Connect to LiveKit Room**

```python
# transport.py — start() method (line 162-190)
async def start(self) -> None:
    self._rtc = rtc
    room = rtc.Room()           # LiveKit Room object (Rust-backed)
    self._room = room

    # Wire up all room event listeners
    self._room_listeners = [
        ("track_subscribed",        self._on_track_subscribed),
        ("data_received",           self._on_data_received),     # RPC / tool calls
        ("disconnected",            self._on_disconnected),
        ("reconnecting",            self._on_reconnecting),
        ("reconnected",             self._on_reconnected),
        ("participant_connected",   self._on_participant_connected),
        ("participant_disconnected",self._on_participant_disconnected),
    ]
    for event, callback in self._room_listeners:
        room.on(event, callback)

    # Establish WebRTC connection using the participant_token
    await room.connect(
        self._session.server_url,       # wss://livekit.mistral.ai
        self._session.participant_token,# LiveKit JWT from /v1/audio/sessions
    )
```

**Step 3: Publish Local Microphone Track**

```python
# transport.py — _publish_input_track() (line 351-375)
async def _publish_input_track(self) -> None:
    # Create a 48kHz mono PCM audio source
    self._audio_source = self._rtc.AudioSource(SAMPLE_RATE, CHANNELS)
    #                                           48000Hz,     1 (mono)

    # Wrap in a named LocalAudioTrack
    track = self._rtc.LocalAudioTrack.create_audio_track(
        "mistral-voice-input",
        self._audio_source,
    )

    # Publish as microphone track type to the room
    options = self._rtc.TrackPublishOptions()
    options.source = self._rtc.TrackSource.SOURCE_MICROPHONE
    self._publication = await self._room.local_participant.publish_track(track, options)
```

**Step 4: Subscribe to Agent Audio Output**

When the agent's audio worker joins the room, it publishes two tracks:
- `"roomio_audio"` — the main TTS speech output.
- `"background_audio"` — filler audio played during tool call execution.

```python
# transport.py — _on_track_subscribed() + _is_agent_output_track() (lines 377-695)
def _on_track_subscribed(self, track, publication=None, _participant=None, *_args):
    if not _is_audio_track(self._rtc, track):
        return
    if not _is_agent_output_track(self._rtc, track, publication):
        return
    self._add_agent_audio(track)     # Start reading PCM from this track

def _is_agent_output_track(rtc, track, publication):
    # Both agent speech and filler audio tracks are agent output
    if "background_audio" in track_names:   # Tool call fillers
        return True
    if "roomio_audio" in track_names:       # Main TTS audio
        return True
```

### The PCM Mixer (20ms Pump Loop)

Agent audio frames are decoded and mixed in a **20ms fixed-block pump loop** using NumPy:

```python
# transport.py — _pump_mixer() (line 444-471)
async def _pump_mixer(self) -> None:
    block = (SAMPLE_RATE // 50) * CHANNELS  # 20ms worth of samples (960)
    block_s = (SAMPLE_RATE // 50) / SAMPLE_RATE  # 0.02s

    while True:
        # Mix all active track buffers via int32 accumulation (prevents int16 overflow)
        acc = np.zeros(block, dtype=np.int32)
        for buf in self._mixer_buffers:
            acc += buf.take(block).astype(np.int32)

        # Clip back to int16 range and emit to audio interface
        mixed = np.clip(acc, -32768, 32767).astype(np.int16)
        await self._emit("audio", mixed.tobytes())

        # Pace the loop to real-time
        next_at += block_s
        delay = next_at - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
```

### RPC Tool Calling over LiveKit Data Channel

```python
# transport.py — constants (lines 55-59)
FUNCTION_TOOL_RPC_METHOD   = "mistral.function_tool"
SESSION_UPDATE_RPC_METHOD  = "mistral.session_update"
FUNCTION_CALL_INVALID_PAYLOAD_CODE = 2400
FUNCTION_CALL_NO_HANDLER_CODE      = 2404
FUNCTION_CALL_HANDLER_FAILED_CODE  = 1

# _register_function_handlers() (line 195-203)
# Registers a local RPC responder on the LiveKit data channel:
self._room.local_participant.register_rpc_method(
    "mistral.function_tool", handler   # Called whenever agent invokes a tool
)

# When agent calls a tool, _dispatch_function_call() runs (line 205-235):
# 1. Deserializes JSON payload into _FunctionCall(name, arguments)
# 2. Looks up handler in self._tools_by_name dict
# 3. Awaits tool.invoke(raw_arguments) → returns str result to agent
# 4. On failure: raises rtc.RpcError with specific error codes
```

### Disconnect Reasons & Reconnection

```python
# transport.py — line 62-79
_TRANSPORT_DISCONNECT_REASONS = {
    "ROOM_DELETED":       "session_ended",
    "AGENT_ERROR":        "agent_error",
    "CONNECTION_TIMEOUT": "timeout",
    "SIGNAL_CLOSE":       "network_failure",
    "MEDIA_FAILURE":      "network_failure",
    "SIP_TRUNK_FAILURE":  "network_failure",  # Telephony-specific
    ...
}
```

On `reconnected`, the transport checks whether the agent re-joined and only fires a fatal error if it did not (accounting for LiveKit's ParticipantDisconnected being emitted before Reconnecting in full-reconnect cycles).

---

## 2. Durable Distributed Workflows — Payload Encryption, Compression & Blob Offloading

### Architecture Overview

`mistralai/extra/workflows/encoding/` is a **layered payload codec**:

```
Raw Python Object
       │
       ▼  (1) JSON serialize (pydantic-core / msgpack)
       │
       ▼  (2) Compress — ZstdCompressor wrapping `zstandard` C library
       │       └─ Only if payload > min_size_bytes (default 1MB)
       │       └─ Metadata packed via msgpack for config-independent decoding
       │
       ▼  (3) Encrypt — AES-GCM via cryptography.hazmat.primitives
       │       └─ 256-bit key from config; 96-bit random nonce per operation
       │       └─ FULL mode encrypts entire payload; PARTIAL encrypts fields
       │
       ▼  (4) Offload to Blob Storage (if payload still > 1MB after encoding)
               └─ Writes to S3/GCS/Azure; replaces payload with {key: "..."}
               └─ Original payload retrieved by key on deserialization
```

### Compression: ZstdCompressor (source truth)

```python
# payload_compressor.py — ZstdCompressor (lines 42-59)
class ZstdCompressor(Compressor):
    def __init__(self, cfg: ZstdCompressionConfig) -> None:
        zstd = _require_zstandard()      # `import zstandard` (Facebook Zstd C binding)
        self._config = cfg
        self._compressor   = zstd.ZstdCompressor(level=cfg.level)   # level 1-22
        self._decompressor = zstd.ZstdDecompressor()

    def compress(self, data: bytes) -> bytes:
        return self._compressor.compress(data)

    def decompress(self, data: bytes) -> bytes:
        return self._decompressor.decompress(data)
```

**Configuration:**
```python
# config.py — ZstdCompressionConfig (lines 52-64)
class ZstdCompressionConfig(BaseModel):
    algorithm: Literal["zstd"] = "zstd"
    level: int = Field(default=3, ge=1, le=22)  # 3 = balanced; 22 = max ratio

class PayloadCompressionConfig(BaseModel):
    min_size_bytes: int = 1024 * 1024            # Only compress payloads >= 1MB
    algorithm_config: AlgorithmConfig = Field(default_factory=ZstdCompressionConfig)
```

After compression, the payload + metadata (algorithm config) is **serialized with msgpack** to produce a self-describing binary blob that can be decoded without knowing the configuration in advance.

### Encryption: AES-GCM (source truth)

```python
# payload_encoder.py — imports (lines 16-26)
try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False
```

**Configuration:**
```python
# config.py — PayloadEncryptionConfig (lines 43-49)
class PayloadEncryptionMode(str, Enum):
    FULL    = "full"     # Encrypt the entire serialized payload
    PARTIAL = "partial"  # Encrypt only specific annotated fields

class PayloadEncryptionConfig(BaseModel):
    mode: PayloadEncryptionMode
    main_key:      Optional[SecretStr] = None  # Active encryption key
    secondary_key: Optional[SecretStr] = None  # Old key — supports key rotation
    # When both keys provided: encrypts with main, decrypts with either
```

Each encrypt call generates a **fresh 96-bit random nonce** (per AESGCM spec) to ensure ciphertext never repeats even for identical plaintexts.

### Blob Offloading: S3 / GCS / Azure (source truth)

```python
# config.py — BlobStorageConfig (lines 13-31)
class BlobStorageConfig(BaseModel):
    storage_provider: StorageProvider = StorageProvider.S3  # s3, gcs, azure

    # Azure Blob Storage
    container_name: Optional[str] = None
    azure_connection_string: Optional[SecretStr] = None
    azure_storage_account_url: Optional[str] = None

    # Google Cloud Storage
    bucket_id: Optional[str] = None

    # Amazon S3 / MinIO / Cloudflare R2 (compatible endpoint)
    bucket_name:          Optional[str] = None
    region_name:          Optional[str] = None
    endpoint_url:         Optional[str] = None   # Custom S3-compatible endpoint
    aws_access_key_id:    Optional[SecretStr] = None
    aws_secret_access_key:Optional[SecretStr] = None

class PayloadOffloadingConfig(BaseModel):
    storage_config: Optional[BlobStorageConfig] = None
    min_size_bytes: int = 1024 * 1024   # Offload payloads >= 1MB (after encoding)
```

When a payload exceeds `min_size_bytes`, the encoder uploads it to blob storage and **replaces it with a pointer**: `{"key": "<storage-path>"}`. During decoding, the pointer is resolved back to the original bytes.

### Usage Pattern

```python
from mistralai.extra.workflows import configure_workflow_encoding
from mistralai.extra.workflows.encoding import (
    WorkflowEncodingConfig,
    PayloadCompressionConfig,
    PayloadEncryptionConfig,
    PayloadEncryptionMode,
    PayloadOffloadingConfig,
    ZstdCompressionConfig,
    BlobStorageConfig,
    StorageProvider,
)

configure_workflow_encoding(WorkflowEncodingConfig(
    payload_compression=PayloadCompressionConfig(
        min_size_bytes=512_000,                           # Compress anything > 512 KB
        algorithm_config=ZstdCompressionConfig(level=9), # High compression level
    ),
    payload_encryption=PayloadEncryptionConfig(
        mode=PayloadEncryptionMode.FULL,
        main_key="base64-encoded-32-byte-aes-key==",
        secondary_key="base64-encoded-old-aes-key==",    # For key rotation
    ),
    payload_offloading=PayloadOffloadingConfig(
        min_size_bytes=2_000_000,                         # Offload blobs > 2 MB
        storage_config=BlobStorageConfig(
            storage_provider=StorageProvider.S3,
            bucket_name="my-workflow-payloads",
            region_name="eu-west-1",
        ),
    ),
))
```

---

## 3. Barge-In (Interruption Handling) — How It Really Works

### The Claim

> "The audio worker halts TTS audio playout instantly, truncates the assistant transcript, and routes the new user query."

### What the SDK Actually Does

**Barge-in is handled on the server side** (inside Mistral's audio worker running in the LiveKit room). The Python SDK's role is:

1. **Continuously pump microphone audio** to the room even while the agent is speaking.
2. **Never gate or mute the input track** based on agent state — the LiveKit worker on Mistral's side detects voice energy on the mic track while it is simultaneously sending TTS audio output.

```python
# session.py — _pump_input_audio() (line 474-501)
async def _pump_input_audio(self) -> None:
    assert self._audio_interface is not None
    assert self._transport is not None
    try:
        await self._transport.wait_for_agent()    # Block until agent joins room
        async for chunk in self._audio_interface.input_audio():
            if self._closing:
                return
            if not self._input_enabled:           # Only honor explicit mute()
                continue
            await self._transport.send_audio(chunk)  # Push mic audio ALWAYS
    # ...

# transport.py — send_audio() (line 266-280)
async def send_audio(self, audio: bytes) -> None:
    samples_per_channel = len(audio) // (BYTES_PER_SAMPLE * CHANNELS)
    frame = self._rtc.AudioFrame(
        data=audio, sample_rate=48_000,
        num_channels=1, samples_per_channel=samples_per_channel,
    )
    result = self._audio_source.capture_frame(frame)  # Push frame to WebRTC track
    if inspect.isawaitable(result):
        await result
```

**The LiveKit room worker** (Mistral's cloud-side agent process) receives:
- **Input track**: continuous microphone PCM from the client.
- It runs VAD (Voice Activity Detection) on that track while TTS audio is playing.
- When it detects voice energy on the input: it cancels the in-flight TTS generation task, truncates the audio buffer being sent on `roomio_audio`, and routes the captured speech back through the STT → LLM loop.

### What the SDK Observes

The client sees the interruption through events:

```python
# session.py — _handle_transport_event() (line 502-545)
async def _handle_transport_event(self, event: VoiceEventName, payload: VoiceEvent):
    if event == "transcript" and isinstance(payload, VoiceAgentTranscriptEvent):
        # Partial transcripts with the same `id` replace each other in the dict
        self._transcript[payload.id] = payload

    if self._audio_interface is not None:
        if event == "audio" and isinstance(payload, bytes):
            await self._audio_interface.output_audio(payload)
            # When the agent interrupts itself: no more "audio" events arrive
            # for the current segment, and a new "agent_state_changed"
            # event with state="listening" fires immediately
```

The `agent_state_changed` event with `state="listening"` (while TTS was previously `"speaking"`) is the observable signal of a barge-in completion. Assistant transcript events with `final=False` for the previous utterance will never be followed by a `final=True` for that segment — it is effectively truncated.

### The `output_audio()` Buffer (DefaultAudioInterface)

```python
# audio.py — output_audio() (line 135-157)
async def output_audio(self, audio: bytes) -> None:
    with self._output_buffer_lock:
        self._output_buffer.extend(audio)
        # Overflow eviction: drop oldest samples if buffer exceeds 300ms
        overflow = len(self._output_buffer) - self._output_buffer_max_bytes
        if overflow > 0:
            aligned_overflow = _align_byte_count(overflow, self._output_frame_width)
            del self._output_buffer[:aligned_overflow]  # Drop from front

# _output_callback() — runs in PortAudio's audio thread (line 197-215)
def _output_callback(self, _input_data, frame_count, time_info, status):
    byte_count = frame_count * self._output_frame_width  # 20ms of samples
    with self._output_buffer_lock:
        data = bytes(self._output_buffer[:byte_count])
        del self._output_buffer[:byte_count]             # Consume from front
    # Zero-fill if buffer is empty (agent stopped speaking — silence)
    if len(data) < byte_count:
        data += b"\x00" * (byte_count - len(data))
    return data, pyaudio.paContinue
```

When the agent stops sending audio events (due to barge-in), the buffer drains naturally and PortAudio emits silence — no explicit stop call needed.

---

## 4. `client.audio.realtime` — `connect()` and `transcribe_stream()`

This is the **lower-level raw STT path** — no voice agent, no LLM, just direct streaming transcription over WebSockets. It does not use LiveKit.

### Protocol: WebSocket + JSON-RPC Events

```python
# connection.py — imports (lines 11-17)
from websockets.asyncio.client import ClientConnection  # websockets >= 13.0
```

The wire protocol is JSON messages over a persistent WebSocket connection. The SDK defines 7 message types:

```python
# connection.py — _MESSAGE_MODELS (lines 66-74)
_MESSAGE_MODELS = {
    "session.created":        RealtimeTranscriptionSessionCreated,
    "session.updated":        RealtimeTranscriptionSessionUpdated,
    "error":                  RealtimeTranscriptionError,
    "transcription.language": TranscriptionStreamLanguage,    # Language detected
    "transcription.segment":  TranscriptionStreamSegmentDelta, # Word/phrase fragment
    "transcription.text.delta":TranscriptionStreamTextDelta,   # Running text update
    "transcription.done":     TranscriptionStreamDone,         # End of utterance
}
```

### `connect()` — Full Source

```python
# realtime.py — connect() implementation
async def connect(
    self,
    model: str,
    audio_format: Optional[AudioFormat] = None,
    target_streaming_delay_ms: Optional[int] = None,  # 80–1200ms; default 240
    ...
) -> RealtimeConnection:
    # 1. Resolve auth headers (API key or service account JWT)
    # 2. Build WebSocket URL: https → wss, http → ws
    ws_url = urlunparse(parsed._replace(scheme="wss"))

    # 3. Open WebSocket handshake
    websocket = await connect(
        ws_url,
        additional_headers=dict(headers),
        open_timeout=open_timeout,
        user_agent_header=user_agent,
    )

    # 4. Read handshake events (session.created) before returning
    session, initial_events = await _recv_handshake(websocket, timeout_ms=timeout_ms)

    # 5. Return a RealtimeConnection with the websocket & session state
    connection = RealtimeConnection(
        websocket=websocket,
        session=session,
        initial_events=initial_events,
    )

    # 6. Optionally configure audio format and streaming delay
    if audio_format is not None or target_streaming_delay_ms is not None:
        await connection.update_session(audio_format, target_streaming_delay_ms=...)

    return connection
```

### `RealtimeConnection` Methods

```python
# connection.py — RealtimeConnection (lines 107-233)
class RealtimeConnection:
    # Properties
    request_id: str        # Server-assigned request ID
    session: RealtimeTranscriptionSession
    audio_format: AudioFormat
    is_closed: bool

    # Audio push methods
    async def send_audio(self, audio_bytes: bytes) -> None:
        # Base64-encodes audio bytes and sends as JSON:
        # {"type": "input_audio_buffer.append", "audio": "<base64>"}
        message = RealtimeTranscriptionInputAudioAppend(
            audio=base64.b64encode(bytes(audio_bytes)).decode("ascii")
        )
        await self._websocket.send(message.model_dump_json())

    async def flush_audio(self) -> None:
        # {"type": "input_audio_buffer.flush"} — tells server to finalize segment
        await self._websocket.send(RealtimeTranscriptionInputAudioFlush().model_dump_json())

    async def end_audio(self) -> None:
        # {"type": "input_audio_buffer.end"} — stream is finished
        await self._websocket.send(RealtimeTranscriptionInputAudioEnd().model_dump_json())

    async def update_session(self, audio_format=None, *, target_streaming_delay_ms=None):
        # Patches the live session config mid-stream
        message = RealtimeTranscriptionSessionUpdateMessage(session=...)
        await self._websocket.send(message.model_dump_json())

    # Event iterator — yields typed events from the server
    async def events(self) -> AsyncIterator[RealtimeEvent]:
        while self._initial_events:    # Replay handshake events
            ev = self._initial_events.popleft()
            self._apply_session_updates(ev)
            yield ev
        async for msg in self._websocket:
            data = json.loads(msg)
            ev = parse_realtime_event(data)  # Tolerant parse → UnknownRealtimeEvent on unknown types
            self._apply_session_updates(ev)  # Keep session state current
            yield ev
```

### `transcribe_stream()` — Full Source (Concurrent Send + Receive)

```python
# realtime.py — transcribe_stream() implementation
async def transcribe_stream(
    self,
    audio_stream: AsyncIterator[bytes],   # Your audio generator
    model: str,
    ...
) -> AsyncIterator[RealtimeEvent]:
    """
    Flow:
      - opens connection
      - streams audio in background (concurrent asyncio task)
      - yields events from the connection
    """
    async with await self.connect(model=model, ...) as connection:

        async def _send() -> None:
            async for chunk in audio_stream:      # Read caller's audio generator
                if connection.is_closed:
                    break
                await connection.send_audio(chunk) # Base64-encode and push
            await connection.flush_audio()         # Signal segment end
            await connection.end_audio()           # Signal stream end

        send_task = asyncio.create_task(_send())   # Run sender concurrently

        try:
            async for event in connection:
                yield event
                # Exit early on terminal events
                if isinstance(event, RealtimeTranscriptionError):
                    break
                if getattr(event, "type", None) == "transcription.done":
                    break
        finally:
            send_task.cancel()        # Clean up background sender
            try:
                await send_task
            except asyncio.CancelledError:
                pass
            await connection.close()  # Close WebSocket (code 1000)
```

### Usage Pattern

```python
import asyncio
from mistralai.client import Mistral
from mistralai.client.models import AudioFormat
from mistralai.extra.realtime import AudioEncoding

async def stream_pcm_file(path: str, chunk_size: int = 4096):
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk
            await asyncio.sleep(0.02)   # 40ms audio chunks at 48kHz

async def main():
    client = Mistral()

    async for event in client.audio.realtime.transcribe_stream(
        audio_stream=stream_pcm_file("call.pcm"),
        model="voxtral-mini-transcribe-realtime-3",
        audio_format=AudioFormat(encoding="pcm_s16le", sample_rate=48000),
        target_streaming_delay_ms=240,   # 240ms: best accuracy/latency balance
    ):
        event_type = getattr(event, "type", None)
        if event_type == "transcription.text.delta":
            print(event.text, end="", flush=True)   # Incremental text
        elif event_type == "transcription.done":
            print()                                  # Final newline
            break
        elif event_type == "error":
            print(f"Error: {event.message}")
            break

asyncio.run(main())
```

### Manual `connect()` for Multi-Segment Control

```python
async def transcribe_multi_segment():
    client = Mistral()

    async with await client.audio.realtime.connect(
        model="voxtral-mini-transcribe-realtime-3",
        target_streaming_delay_ms=480,  # Higher delay = more accurate
    ) as conn:
        print(f"Session: {conn.request_id}, Format: {conn.audio_format}")

        # Segment 1
        with open("segment1.pcm", "rb") as f:
            while chunk := f.read(4096):
                await conn.send_audio(chunk)
        await conn.flush_audio()   # Finalize segment 1

        # Segment 2 — mid-stream config change
        await conn.update_session(target_streaming_delay_ms=240)  # Reduce delay
        with open("segment2.pcm", "rb") as f:
            while chunk := f.read(4096):
                await conn.send_audio(chunk)
        await conn.flush_audio()
        await conn.end_audio()     # Signal entire stream done

        async for event in conn:
            if hasattr(event, "text"):
                print(event.text, end="")
```

---

## 5. Built-In Provider Tools — Source Truth

Provider tools are declarations sent to the backend that give the voice agent access to built-in Mistral capabilities. They are defined in `mistralai/extra/voice_agents/tools.py`.

### Tool Type Hierarchy (from source)

```python
# tools.py — Tool union (lines 312-320)
Tool = (
    WebSearchTool       # Web search via Mistral's search backend
    | DocumentLibraryTool  # Semantic search over document libraries
    | CustomConnector      # Enterprise connector gateway
    | FunctionTool         # Locally-executed Python function
    | TransferCallTool     # SIP call transfer (telephony only)
    | HangUpTool           # SIP call termination (telephony only)
    | VoiceAgentTool       # Wrapper adding voice-specific fields (filler, timeout)
)
```

### `VoiceAgentTool` — The Wrapper (source)

```python
# tools.py — VoiceAgentTool (lines 64-148)
class VoiceAgentTool(BaseModel):
    """SDK convenience composition; the backend accepts only flat declarations.

    VoiceAgentTool(tool=WebSearchTool(), filler={"text": "Searching"})
    serializes as {"type": "web_search", "filler": {"type": "static", "text": "Searching"}}.
    """
    tool: SharedTool | BaseModel       # The actual tool declaration
    filler: StaticToolFiller | None    # Short spoken phrase during tool execution
    timeout_ms: int | None = Field(default=None, ge=1, le=4294967295)

    def to_wire(self) -> dict[str, Any]:
        # Flattens into wire format: {"type": "web_search", "filler": {...}}
        return self.model_dump(mode="json", exclude_none=True)
```

### Provider Tool 1: `WebSearchTool`

Grants the voice agent the ability to perform live web searches.

```python
from mistralai.extra.voice_agents import VoiceAgentTool, WebSearchTool

tools = [
    VoiceAgentTool(
        tool=WebSearchTool(),
        filler={"text": "Searching the web for that."},
    )
]
# Wire format sent to Mistral backend:
# {"type": "web_search", "filler": {"type": "static", "text": "Searching the web for that."}}
```

### Provider Tool 2: `DocumentLibraryTool`

Performs semantic vector search against document libraries uploaded to Mistral AI Studio.

```python
from mistralai.extra.voice_agents import VoiceAgentTool, DocumentLibraryTool

tools = [
    VoiceAgentTool(
        tool=DocumentLibraryTool(),     # Searches all libraries the agent has access to
        filler={"text": "Checking our knowledge base."},
    )
]
```

### Provider Tool 3: `CustomConnector`

Queries enterprise connectors registered in Mistral's Connector Gateway (SharePoint, Salesforce, custom REST endpoints, etc.).

```python
from mistralai.extra.voice_agents import (
    VoiceAgentTool,
    CustomConnector,
    ToolConfiguration,
)

tools = [
    CustomConnector(
        connector_id="conn_01abc...",
        tool_configuration=ToolConfiguration(
            include=["search", "retrieve"],   # Expose only specific capabilities
        ),
    )
    # Or with a filler:
    VoiceAgentTool(
        tool=CustomConnector(connector_id="conn_01abc..."),
        filler={"text": "Querying the enterprise system."},
    )
]
```

### Provider Tool 4: `TransferCallTool` (Telephony — SIP only)

Sends a SIP REFER to transfer the call to another number or SIP URI.

```python
from mistralai.client.models import TransferCallTool, TransferTarget, PhoneDestination, SipDestination

tools = [
    TransferCallTool(
        destinations=[
            TransferTarget(
                step_name="Human agent",
                destination=PhoneDestination(phone_number="+33123456789"),
            ),
            TransferTarget(
                step_name="Billing department",
                destination=SipDestination(uri="sip:billing@pbx.company.com"),
            ),
        ]
    )
]
```

### Provider Tool 5: `HangUpTool` (Telephony — SIP only)

Instructs the agent to terminate the call when the conversation is complete.

```python
from mistralai.client.models import HangUpTool

tools = [HangUpTool()]
# The agent calls this when it determines the user's request is fully resolved.
```

### Full Session with All Provider Tools

```python
from mistralai.client import Mistral
from mistralai.client.models import (
    AudioLLMConfig, AudioSTTConfig, AudioTTSConfig,
    WebSearchTool, DocumentLibraryTool, TransferCallTool,
    TransferTarget, PhoneDestination, HangUpTool,
)
from mistralai.extra.voice_agents import (
    DefaultAudioInterface, VoiceAgentTool,
    CustomConnector, ToolConfiguration, function_tool,
)
import asyncio, os

@function_tool(filler={"text": "Fetching account details."}, timeout_ms=8000)
async def get_account(account_id: str) -> str:
    """Look up account information by ID."""
    return f"Account {account_id}: Gold tier, active."

async def main():
    async with Mistral() as client:
        session = await client.audio.sessions.init(
            llm=AudioLLMConfig(model="mistral-small-latest"),
            stt=AudioSTTConfig(model="voxtral-mini-transcribe-realtime-3"),
            tts=AudioTTSConfig(voice_id="gb_oliver_excited"),
            instructions="You are a banking voice assistant. Transfer to humans when escalation is needed.",
            tools=[
                # Web search — find answers online
                VoiceAgentTool(tool=WebSearchTool(), filler={"text": "Checking online."}),
                # Document library — search internal policy docs
                VoiceAgentTool(tool=DocumentLibraryTool(), filler={"text": "Looking in our docs."}),
                # Enterprise connector — query CRM
                CustomConnector(connector_id="crm_connector_id"),
                # Custom function
                get_account,
                # Telephony — transfer or hang up
                TransferCallTool(destinations=[
                    TransferTarget(step_name="Human", destination=PhoneDestination(phone_number="+1800000000")),
                ]),
                HangUpTool(),
            ],
            audio_interface=DefaultAudioInterface(),
        )

        session.on("tool_call_started", lambda e: print(f"Tool: {e.name}"))
        session.on("tool_call_ended",   lambda e: print(f"Done: {e.name} → {e.result}"))

        async with session:
            await session.wait()

asyncio.run(main())
```
