# 48-Hour Learning Guide for This Loan Approval Voice Agent

Use this guide with the **Scan → Break → Build** framework to learn this repository fast enough to fix real issues without trying to master every technology first.

## What you are learning

This project is not one technology. It is a realtime AI application made of five connected layers:

| Layer | Main tech | Where to read first | What you must understand |
|---|---|---|---|
| Frontend UI | React + Vite | `Frontend/src/App.jsx`, `Frontend/src/components/videoCallScreen.jsx` | How the browser joins a session, listens for events, captures media, and renders call state. |
| Video/session transport | VideoSDK + browser media APIs + SSE | `Frontend/src/hooks/videoSDKSession.js`, `Backend/api/routes/session.py`, `Backend/api/routes/videosdk.py` | How room credentials, EventSource events, recording, upload, and stage changes move between frontend and backend. |
| Backend API | FastAPI | `Backend/main.py`, `Backend/api/routes/session.py` | How app startup, lifecycle hooks, routers, and endpoints are wired. |
| Agent workflow | SharedState + moderator + agents | `Backend/models/shared_state.py`, `Backend/core/langgraph_engine.py`, `Backend/agents/*` | How a customer advances through stages and how agents mutate state. |
| AI/media services | STT, TTS, LLM, vision, bureau mock | `Backend/agents/stt_pipeline.py`, `Backend/services/tts_service.py`, `Backend/services/llm_gateway.py`, `Backend/agents/vision_agent.py`, `Backend/services/bureau_client.py` | Where latency comes from and which services are critical vs optional for a demo. |

## The 48-hour path

### Phase 1 — Scan, first 4 hours

Goal: understand **what each subsystem does**, not every implementation detail.

#### Hour 0–1: Run the dependency map mentally

Read only these files in this order:

1. `README.md` for the advertised architecture.
2. `ENGINEERING_REPORT.md` for known root causes and the recommended realtime redesign.
3. `Backend/main.py` for startup, router registration, and warmup behavior.
4. `Backend/models/shared_state.py` for the session data model and stage sequence.
5. `Backend/core/langgraph_engine.py` for stage transitions.
6. `Frontend/src/components/videoCallScreen.jsx` for the current browser-side media/event pipeline.

Do not edit code yet. Write down answers to these questions:

- What starts a session?
- Where does the current stage live?
- How does the frontend learn that a stage changed?
- Where is user speech converted into backend state?
- Where is agent speech converted into playable audio?

#### Hour 1–2: Scan the frontend flow

Focus on:

- `EventSource` lifecycle.
- `getUserMedia` lifecycle.
- speech recognition start/restart behavior.
- document upload UI.
- recording upload.

Deliverable: draw this one-line flow:

```text
JoinPage -> videoSDKSession hook -> VideoCallScreen -> EventSource events -> UI + audio playback
```

#### Hour 2–3: Scan the backend flow

Focus on:

- FastAPI lifespan startup.
- `/session/create`, `/session/{token}/join`, `/session/{call_id}/events`.
- Redis state reads/writes.
- moderator `advance_stage`.
- conversation agent `AI_AGENT_SPEECH` and `TTS_AUDIO_READY` events.

Deliverable: draw this one-line flow:

```text
join -> SharedState -> moderator stage -> EventBus -> ConversationAgent -> Redis pubsub/SSE -> frontend
```

#### Hour 3–4: Scan only the technologies you need

Use crash-course style learning, but keep it project-scoped:

- React hooks: enough to understand `useEffect`, `useState`, `useRef`, cleanup functions.
- FastAPI: enough to understand routers, Pydantic models, async endpoints, lifespan.
- Redis pub/sub: enough to understand event streaming and state caching.
- Browser media APIs: enough to understand mic permission, `MediaRecorder`, `SpeechRecognition`, `AudioContext`.
- WebSocket/SSE concepts: enough to understand why SSE is okay for events but not ideal for realtime audio.

Stop after 4 hours even if you feel incomplete.

## Phase 2 — Break, next 8 hours

Goal: intentionally break small parts, observe symptoms, then fix them. Use tiny experiments, not big rewrites.

### Exercise A — Break a stage transition

1. Temporarily change a stage opener in `Backend/agents/conversation_agents.py`.
2. Start a session.
3. Confirm the frontend receives a different `AI_AGENT_SPEECH` event.
4. Revert your temporary change.

What you learn: EventBus → ConversationAgent → Redis/SSE → frontend.

### Exercise B — Break a gate safely

1. In a temporary branch or uncommitted scratch change, make `_identity_gate` always return `False` in `Backend/core/langgraph_engine.py`.
2. Send a transcript with a name.
3. Watch the system re-ask instead of advancing.
4. Revert the change.

What you learn: why stage progression depends on `SharedState`, not just transcript text.

### Exercise C — Break frontend event cleanup

1. Find the `EventSource` setup in `Frontend/src/components/videoCallScreen.jsx`.
2. Comment out cleanup in a scratch change.
3. Reload in development and observe duplicate events or repeated audio.
4. Revert immediately.

What you learn: why realtime apps become laggy when listeners are duplicated.

### Exercise D — Break TTS cache path

1. Temporarily change one static message text so it no longer exactly matches the cache key.
2. Observe whether TTS falls through to fresh synthesis.
3. Revert.

What you learn: why exact prompt/message matching matters for low-latency audio.

## Phase 3 — Build, remaining 36 hours

Goal: make small production-style improvements without following a tutorial.

### Build task 1 — Add latency instrumentation

Add timestamps for:

- frontend speech detected.
- transcript sent/received.
- stage advanced.
- `AI_AGENT_SPEECH` published.
- `TTS_AUDIO_READY` published.
- audio playback started.

Acceptance criteria:

- Each event has `call_id`, `stage`, and `ts`.
- You can compute time-to-first-agent-text and time-to-first-audio from logs.

### Build task 2 — Split one frontend responsibility

Extract one hook from `videoCallScreen.jsx`, preferably one of:

- `useSessionEvents(callId)` for EventSource handling.
- `useSpeechRecognition(callId, stage)` for browser STT.
- `useAgentAudioPlayer()` for TTS playback and interruption.

Acceptance criteria:

- No behavior change.
- Cleanup is explicit.
- Parent component has fewer effects.

### Build task 3 — Create document verification V1

Build one endpoint that accepts a document image and returns a structured mock extraction:

```json
{
  "name": "...",
  "dob": "...",
  "id_type": "pan|aadhaar",
  "id_number": "...",
  "confidence": 0.0
}
```

Then compare it with `mock_bureau` data.

Acceptance criteria:

- No OTP stage.
- Response includes matched identity and confidence.
- Low-confidence extraction does not crash the session.

### Build task 4 — Replace one sequential wait

Find one place where the system waits for non-critical work before continuing. Convert it to a background task only if failure is non-fatal.

Good candidates:

- session-specific TTS precompute.
- non-critical vision checks.
- offer explanation generation after deterministic offer values exist.

Acceptance criteria:

- User-visible event is emitted before slow optional work finishes.
- Failure is logged and does not block the demo path.

## How to choose what to learn first

Use this priority order:

1. **React effects and cleanup** — because duplicated listeners create visible demo bugs.
2. **FastAPI async endpoints** — because most backend flow starts here.
3. **SharedState and moderator gates** — because all business workflow bugs end here.
4. **Redis pub/sub and SSE** — because this is the current realtime event path.
5. **Browser media and audio** — because this controls perceived latency.
6. **STT/TTS/LLM internals** — learn only after you know where they sit in the loop.

## Debugging checklist for this project

When something feels slow or broken, ask these in order:

1. Did the frontend event listener mount more than once?
2. Did the browser get mic permission immediately?
3. Was a transcript created, and did it include confidence?
4. Did `SharedState` update in Redis?
5. Did the moderator gate pass?
6. Did `AI_AGENT_SPEECH` publish before TTS finished?
7. Was `TTS_AUDIO_READY` a cache hit or fresh synthesis?
8. Did the frontend audio player start playback or get blocked by autoplay rules?

## What not to learn deeply yet

For hackathon-speed project repair, avoid deep-diving these until the core loop is stable:

- Kubernetes or distributed deployment.
- Full LangGraph theory.
- RabbitMQ patterns that are not used on the critical path.
- Advanced credit-risk modeling.
- Perfect OCR/vision modeling.
- Enterprise observability stacks.

## Your first three concrete fixes after learning

1. Add latency spans across Speech → STT → Moderator → TTS → Playback.
2. Split `videoCallScreen.jsx` event/audio logic into hooks to reduce duplicate listeners and rerenders.
3. Implement document-image identity verification against `mock_bureau` as the OTP replacement.

If you complete those three, you will understand enough of the project to fix the largest demo reliability and latency problems.
