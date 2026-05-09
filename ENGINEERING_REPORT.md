# Realtime Voice Agent Root Cause Analysis & Demo-First Redesign

## 1) Root Cause Analysis
- **Sequential turn loop**: speech is captured client-side, posted as full text, then backend does stage logic then TTS; this introduces hard turn boundaries and dead air.
- **Mixed transport model**: app uses VideoSDK + SSE + HTTP uploads simultaneously, creating duplicated state/event channels.
- **Overgrown stage machine**: stage orchestration still references removed/legacy Aadhaar OTP semantics in multiple files.
- **Heavy frontend component**: `videoCallScreen.jsx` is a monolith with microphone, speech recognition, event source, recording, upload, rendering, and document UX in one module.
- **Cold path variance**: model warmup is inconsistent (LLM disabled on startup, TTS warmup in multiple places).

## 2) Critical Latency Bottlenecks (current)
- Mic init (`getUserMedia`) delayed until interaction and mixed with video setup.
- Browser Web Speech API restarts on errors causing jitter loops.
- STT is non-streaming (utterance/chunk based), no partial server-side hypothesis.
- LLM call starts only after final transcript parse.
- TTS is non-streaming and mostly complete-file playback.
- SSE pushes text/audio events but no backpressure signaling.

## 3) Architecture Flaws
- Duplicate warmup logic across `main.py` and `session.py` preload helpers.
- State machine progression and conversational prompts tightly coupled to enum literals.
- Multiple legacy references (OTP/liveness naming) remain despite flow changes.
- Event fan-out split between Redis pubsub + in-process EventBus without explicit ownership boundaries.

## 4) Dead/Bloat Candidates
- `Backend/test_lg.py`, `Backend/test_lg2.py`, `Backend/test_lg_minimal.py`, `Backend/test_llm.py` are ad-hoc and not integrated with `Backend/tests`.
- Large checked-in `Backend/documents/**` demo artifacts should be externalized.
- `Frontend/src/components/videoCallScreen.jsx` should be split into feature modules/hooks.

## 5) Implemented Simplification in this change
- Removed OTP stage references from stage enum and progression.
- Removed OTP-specific re-ask/openers and session TTS precompute string.
- Updated OVD confirmation to proceed directly to identity fields.

## 6) New Demo-First Flow (recommended)
1. Join session -> warm mic + WS immediately.
2. Upload document image first (or in parallel with greeting).
3. Vision extraction: `name`, `dob`, `id_type`, `id_number`, `address`.
4. Bureau match + confidence score.
5. Continue with income/purpose/risk/offer.

## 7) Voice Pipeline Redesign (target)
- Browser AudioWorklet PCM 16k mono frames (20ms) -> WS binary frames.
- Server VAD (Silero) for speech gating + barge-in detection.
- Streaming Whisper/faster-whisper partial transcripts.
- Token-stream LLM responses.
- Sentence/chunk incremental TTS playback.
- Interrupt handler: user speech immediately cancels active TTS stream.

## 8) Websocket Lifecycle Proposal
- One primary **Realtime WS** per call_id for: `audio_in`, `partial_transcript`, `agent_tokens`, `tts_chunks`, `barge_in`.
- Keep SSE only as fallback for non-audio state updates during migration.
- Add per-session bounded queues and drop policy for stale partials.

## 9) Frontend Refactor Plan
- Split `videoCallScreen` into:
  - `useRealtimeTransport`
  - `useMicPipeline`
  - `useAgentAudioPlayer`
  - `useSessionStageState`
  - `DocumentUploadPanel`
- Move ephemeral debug state to refs; store only UI-relevant state in React state.
- Memoize stage panels; isolate frequently changing transcript text from parent rerenders.

## 10) Backend Refactor Plan
- Introduce `RealtimeOrchestrator` singleton with explicit lifecycle: `init()`, `warmup()`, `session_context(call_id)`.
- Merge stage-open prompts and stage gating definitions into a single canonical config.
- Replace scattered `asyncio.create_task` calls with tracked task group per session.
- Add structured latency spans: `mic->stt`, `stt->llm_first_token`, `llm->tts_first_byte`, `tts->playback_start`.

## 11) Mock Bureau Alignment for Document Verification
- Ensure dataset includes normalized keys used by vision extraction:
  - `full_name`, `dob_iso`, `id_type`, `id_number`, `address_line1`, `pincode`.
- Add fuzzy match scoring:
  - name similarity (Jaro-Winkler), dob exact, id exact/partial, address token overlap.

## 12) Immediate Fixes (next 48h)
- Add document-extraction endpoint returning structured JSON and confidence.
- Add per-turn latency logging with correlation id.
- Pre-initialize audio context and mic permissions on join page click.

## 13) Long-term Fixes (2-4 weeks)
- Migrate to unified realtime transport (WS/WebRTC).
- Streaming STT/LLM/TTS pipeline with interruption.
- Replace monolithic component and add soak test harness.

## 14) Risk Analysis
- **High**: migration to streaming may destabilize demos if done all-at-once.
- **Mitigation**: dual-path rollout (existing SSE path fallback).
- **Medium**: model warmup on constrained RAM.
- **Mitigation**: lightweight model profile + readiness gates.

## 15) Before vs After (this patch)
- Before: OVD -> Aadhaar OTP -> Identity.
- After: OVD -> Identity directly (reduced friction, lower turn count, faster onboarding).
