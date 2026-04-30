"""
Session API Routes
──────────────────
POST /api/v1/session/create              – Create new loan session + VideoSDK room
GET  /api/v1/session/{id}                – Get current session state
POST /api/v1/session/{id}/join           – Customer joins; returns VideoSDK token
POST /api/v1/session/{id}/end            – Gracefully end session
GET  /api/v1/session/{id}/events         – SSE stream for real-time stage updates
POST /api/v1/session/{id}/upload-recording – Upload client-side recording
GET  /api/v1/session/tts/audio/{filename}  – Serve TTS audio files
POST /api/v1/session/{id}/snapshot       – Receive canvas snapshot for vision agent
"""

import uuid
import asyncio
import logging
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from models.shared_state import SharedState, SessionMeta, SessionStage
from services.videosdk_service import videosdk_service
from core.redis_client import redis_client
from core.langgraph_engine import moderator_engine
from core.config import settings
from core.database import db  # PostgreSQL async session

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request / Response schemas ────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    customer_phone: str
    campaign_id: str | None = None
    channel: str = "sms"       # sms | whatsapp | email


class CreateSessionResponse(BaseModel):
    call_id: str
    session_token: str
    join_url: str              # Short URL sent to customer via SMS/WA
    videosdk_room_id: str
    expires_at: str


class JoinSessionResponse(BaseModel):
    call_id: str
    videosdk_room_id: str
    videosdk_token: str        # Customer's JWT for VideoSDK SDK
    participant_id: str
    stage: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/create", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest):
    """
    Backend-initiated: agent / campaign system creates the session,
    sends the join_url to the customer via SMS/WhatsApp.
    """
    call_id       = str(uuid.uuid4())
    session_token = str(uuid.uuid4())

    # 1. Create VideoSDK room
    try:
        room_data = await videosdk_service.create_room(call_id)
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        upstream_detail = exc.response.text.strip()
        if status_code in (401, 403):
            raise HTTPException(
                status_code=502,
                detail=(
                    "VideoSDK rejected the configured API credentials. "
                    "Set valid VIDEOSDK_API_KEY and VIDEOSDK_SECRET_KEY in Backend/.env, "
                    "then restart the backend."
                ),
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"VideoSDK room creation failed with HTTP {status_code}: {upstream_detail}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to reach VideoSDK while creating the room.",
        ) from exc
    room_id   = room_data["roomId"]

    # 2. Initialise Shared State
    meta = SessionMeta(
        call_id=call_id,
        session_token=session_token,
        videosdk_room_id=room_id,
        videosdk_token=videosdk_service.generate_token(room_id=room_id),
    )
    state = SharedState(session_meta=meta)
    await redis_client.set_state(state.redis_key(), state.to_json())
    await redis_client.register_session(call_id, {"call_id": call_id, "room_id": room_id, "stage": "INIT"})

    # 3. Persist audit record to PostgreSQL
    await db.execute(
        """
        INSERT INTO sessions (call_id, session_token, room_id, customer_phone, campaign_id, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        call_id, session_token, room_id, req.customer_phone, req.campaign_id,
        datetime.now(timezone.utc),
    )

    join_url = f"{settings.ALLOWED_ORIGINS[0]}/join/{session_token}"
    expires_at = "30 minutes from now"

    logger.info(f"Session created: {call_id} | room: {room_id}")
    return CreateSessionResponse(
        call_id=call_id,
        session_token=session_token,
        join_url=join_url,
        videosdk_room_id=room_id,
        expires_at=expires_at,
    )


@router.get("/active")
async def active_sessions():
    """Return sessions currently registered in Redis."""
    return await redis_client.list_active_sessions()


@router.get("/{call_id}")
async def get_session(call_id: str):
    """Return current shared state for a session (sanitised for frontend)."""
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    state = SharedState.from_json(raw)
    return {
        "call_id": call_id,
        "stage": state.current_stage,
        "customer_name": state.customer_identity.name,
        "liveness_passed": state.customer_identity.liveness_passed,
        "risk_band": state.financial_data.risk_band,
        "offer": {
            "eligible_amount": state.final_offer.eligible_amount,
            "interest_rate": state.final_offer.interest_rate,
            "acceptance_status": state.final_offer.acceptance_status,
        } if state.final_offer.eligible_amount else None,
    }


@router.post("/{session_token}/join", response_model=JoinSessionResponse)
async def join_session(session_token: str, request: Request):
    """
    Customer clicks the link → frontend calls this endpoint.
    Returns the VideoSDK token + room_id so the React SDK can connect.
    """
    # Look up session by token
    raw = await db.fetchrow(
        "SELECT call_id, room_id FROM sessions WHERE session_token = $1 AND ended_at IS NULL",
        session_token,
    )
    if not raw:
        raise HTTPException(status_code=404, detail="Invalid or expired session link")

    call_id = str(raw["call_id"])
    room_id = raw["room_id"]

    # Validate room is still active
    if not await videosdk_service.validate_room(room_id):
        raise HTTPException(status_code=410, detail="Session has expired")

    # Generate participant-specific token
    participant_id = f"customer-{uuid.uuid4().hex[:8]}"
    token = videosdk_service.generate_token(
        permissions=["allow_join"],
        room_id=room_id,
        participant_id=participant_id,
    )

    # Update state with participant ID and geo
    should_start_session = False
    state_raw = await redis_client.get_state(f"session:{call_id}:state")
    if state_raw:
        state = SharedState.from_json(state_raw)
        should_start_session = state.current_stage == SessionStage.INIT
        if should_start_session:
            state.current_stage = SessionStage.GREETING_CONSENT
            state.version += 1
        state.session_meta.videosdk_participant_id = participant_id
        state.session_meta.ip_address = request.client.host if request.client else None
        await redis_client.set_state(state.redis_key(), state.to_json())

    # Start recording immediately (RBI requirement)
    try:
        rec = await videosdk_service.start_recording(room_id, call_id)
        if state_raw:
            state.session_meta.videosdk_recording_id = rec.get("id")
            await redis_client.set_state(state.redis_key(), state.to_json())
    except Exception as e:
        logger.warning(f"Recording start failed (non-fatal), assuming client-side recording: {e}")
        if state_raw:
            state.session_meta.videosdk_recording_id = f"client_rec_{call_id}"
            await redis_client.set_state(state.redis_key(), state.to_json())

    # Kick off the opening stage once. React StrictMode and page refreshes can call /join twice.
    if should_start_session and await redis_client.set_once(f"session:{call_id}:started"):
        await moderator_engine.start_session(call_id)
    else:
        logger.info(f"Session already started, skipping duplicate kickoff: {call_id}")

    logger.info(f"Customer joined: {call_id} | participant: {participant_id}")
    return JoinSessionResponse(
        call_id=call_id,
        videosdk_room_id=room_id,
        videosdk_token=token,
        participant_id=participant_id,
        stage=SessionStage.GREETING_CONSENT,
    )


@router.post("/{call_id}/end")
async def end_session(call_id: str):
    """Gracefully end session – stop recording, archive state."""
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    state = SharedState.from_json(raw)
    if state.current_stage not in (SessionStage.COMPLETED, SessionStage.ESCALATED):
        state.current_stage = SessionStage.ABANDONED
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

    await redis_client.set_once(f"session:{call_id}:stopped")

    # Stop recording with retries (exponential backoff)
    recording_stopped = False
    recording_error = None
    
    rec_id = state.session_meta.videosdk_recording_id
    room_id = state.session_meta.videosdk_room_id
    
    # Only try to stop cloud recording if it's a real VideoSDK recording (not our client-side fallback)
    if rec_id and not rec_id.startswith("client_rec_"):
        max_retries = 3
        retry_delays = [2, 4, 8]  # seconds
        for attempt in range(max_retries):
            try:
                await videosdk_service.stop_recording(room_id)
                recording_stopped = True
                logger.info(f"Recording stopped successfully for call {call_id}")
                break
            except httpx.HTTPStatusError as e:
                recording_error = f"HTTP {e.response.status_code}: {e.response.text}"
                logger.warning(f"Stop recording attempt {attempt + 1}/{max_retries} failed for call {call_id}: {recording_error}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delays[attempt])
            except Exception as e:
                recording_error = str(e)
                logger.warning(f"Stop recording attempt {attempt + 1}/{max_retries} failed for call {call_id}: {recording_error}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delays[attempt])
    else:
        # For client-side recordings, we consider it "stopped" successfully as far as the backend is concerned
        recording_stopped = True

    # Mark ended in PostgreSQL
    await db.execute(
        "UPDATE sessions SET ended_at = $1, final_stage = $2 WHERE call_id = $3",
        datetime.now(timezone.utc),
        state.current_stage.value,
        call_id,
    )

    await redis_client.unregister_session(call_id)
    
    # Return detailed response
    response = {
        "status": "ended",
        "call_id": call_id,
        "final_stage": state.current_stage.value,
        "recording_stopped": recording_stopped,
    }
    
    if not recording_stopped and recording_error:
        response["recording_error"] = recording_error
        logger.error(f"Session {call_id} ended but recording stop failed after retries: {recording_error}")
    
    return response


@router.get("/{call_id}/events")
async def session_events(call_id: str):
    """
    Server-Sent Events stream.
    Frontend subscribes to get real-time stage updates without polling.
    """
    async def event_generator():
        pubsub = await redis_client.subscribe(f"session:{call_id}:events")
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield f"data: {message['data']}\n\n"
                await asyncio.sleep(0.1)
        finally:
            await pubsub.unsubscribe()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

# ── Browser-Based Speech Transcript (replaces paid VideoSDK transcription) ────

class TranscriptPayload(BaseModel):
    text: str           # Recognized speech text from Web Speech API
    confidence: float   # Recognition confidence (0-1)
    timestamp: float    # Unix timestamp


@router.post("/{call_id}/transcript")
async def receive_transcript(call_id: str, payload: TranscriptPayload):
    """
    Receive speech transcripts from the browser's Web Speech API.
    This replaces VideoSDK's paid transcription webhook.
    The transcript is queued to the STT pipeline for entity extraction
    and then forwarded to the Conversation Agent.
    """
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    if not payload.text.strip():
        return {"status": "ignored", "reason": "empty transcript"}

    # Queue to STT pipeline (same format as the VideoSDK webhook used)
    from core.rabbitmq_client import rabbitmq_client
    await rabbitmq_client.publish_task("stt_pipeline", {
        "call_id":             call_id,
        "raw_transcript":      payload.text,
        "videosdk_confidence": payload.confidence,
        "timestamp":           payload.timestamp,
        "action":              "process_utterance",
        "source":              "browser_speech_api",
    })

    # Also show as live caption via SSE
    await redis_client.publish(f"session:{call_id}:events", {
        "event":   "LIVE_CAPTION",
        "text":    payload.text,
        "call_id": call_id,
    })

    # Use browser confidence if available, otherwise backend will estimate it later
    display_conf = payload.confidence if payload.confidence > 0 else 0.95
    logger.info(f"Browser transcript [{call_id}]: \"{payload.text[:60]}\" (conf={display_conf:.2f} {'[estimated]' if payload.confidence <= 0 else ''})")
    return {"status": "received", "call_id": call_id}


# ── Client-Side Recording Upload ─────────────────────────────────────────────

RECORDINGS_DIR = Path("recordings")
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/{call_id}/upload-recording")
async def upload_recording(call_id: str, file: UploadFile = File(...)):
    """
    Receive client-side recorded video (WebM) and store locally.
    The browser MediaRecorder API captures the video call and uploads it here.
    """
    # Validate session exists
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    # Create session-specific recording directory
    session_dir = RECORDINGS_DIR / call_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # Save recording
    timestamp = int(time.time())
    filename = f"recording_{call_id}_{timestamp}.webm"
    filepath = session_dir / filename

    async with aiofiles.open(filepath, "wb") as f:
        content = await file.read()
        await f.write(content)

    file_size_mb = len(content) / (1024 * 1024)
    logger.info(
        f"Recording saved: {filepath} ({file_size_mb:.1f} MB) for call {call_id}"
    )

    return {
        "status": "saved",
        "call_id": call_id,
        "filename": filename,
        "size_mb": round(file_size_mb, 2),
    }


# ── TTS Audio Serving ─────────────────────────────────────────────────────────

from services.tts_service import TTS_AUDIO_DIR


@router.get("/tts/audio/{filename}")
async def serve_tts_audio(filename: str):
    """
    Serve TTS-generated audio files to the frontend.
    Audio is generated by edge-tts / ElevenLabs and stored in a temp directory.
    """
    # Security: prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    audio_path = TTS_AUDIO_DIR / filename
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    # Determine media type based on extension
    media_type = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"

    return FileResponse(
        str(audio_path),
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=300"},
    )


# ── Canvas Snapshot for Vision Agent ──────────────────────────────────────────


class SnapshotPayload(BaseModel):
    image_data: str   # base64-encoded JPEG from canvas.toDataURL()


@router.post("/{call_id}/snapshot")
async def receive_snapshot(call_id: str, payload: SnapshotPayload):
    """
    Receive a canvas snapshot from the frontend for vision-agent processing.
    The frontend captures the customer's video element via canvas and sends
    the base64-encoded JPEG here. We store it in Redis for the vision agent
    to pick up.
    """
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    # Store snapshot in Redis with short TTL (vision agent picks it up)
    import base64
    try:
        # Validate it's valid base64
        image_b64 = payload.image_data
        if image_b64.startswith("data:image"):
            image_b64 = image_b64.split(",", 1)[1]
        base64.b64decode(image_b64)  # validate
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data")

    # Store in Redis for vision agent to consume (TTL: 30 seconds)
    await redis_client.set_state(
        f"session:{call_id}:snapshot",
        json.dumps({"image": image_b64, "timestamp": time.time()}),
        ttl=30,
    )

    # Trigger vision agent processing via RabbitMQ
    from core.rabbitmq_client import rabbitmq_client
    await rabbitmq_client.publish_task("vision", {
        "call_id": call_id,
        "action": "run_liveness_age_check",
        "source": "canvas_snapshot",
    })

    logger.debug(f"Snapshot received and queued for vision processing: {call_id}")
    return {"status": "received", "call_id": call_id}
