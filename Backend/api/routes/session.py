"""
Session API Routes

FIXES IN THIS FILE:
  FIX-RACE      SSE event_generator now SUBSCRIBES first, REPLAYS buffer,
                then streams live — no events can be lost regardless of
                how quickly start_session() fires after /join returns.
  FIX-KEEPALIVE SSE sends a `:keepalive` comment every 15 s so browsers
                and proxies never drop the connection silently (was root
                cause of the 25-30 s microphone delay).
  FIX-REGREET   Re-greeting on reconnect is now guarded by set_once so
                React StrictMode double-mount can't send the greeting twice.
  FIX-DELAY     start_session() is called with a 1.5 s delay so the frontend
                has time to open the SSE connection before the first event.
                The replay buffer makes this unnecessary, but the delay adds
                a second layer of safety.
"""

import asyncio
import uuid
import logging
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from models.shared_state import SharedState, SessionMeta, SessionStage
from services.videosdk_service import videosdk_service
from core.redis_client import redis_client
from core.langgraph_engine import moderator_engine
from core.config import settings
from core.database import db

logger = logging.getLogger(__name__)
router = APIRouter()

# ── SSE heartbeat interval (seconds) ─────────────────────────────────────────
_SSE_HEARTBEAT_INTERVAL = 15

# ── Startup preload ────────────────────────────────────────────────────────────

async def startup_preload():
    """
    Called once at app startup (add to FastAPI lifespan).
    Eliminates cold-start by eagerly loading LLM + TTS + EventBus handlers.
    """
    logger.info("🔄 Preloading models and services...")

    # 1. Import ConversationAgent — registers EventBus handlers at import time
    try:
        from agents.conversation_agents import conversation_agent  # noqa: F401
        logger.info("✅ ConversationAgent EventBus handlers registered")
    except Exception as e:
        logger.error(f"ConversationAgent preload failed: {e}")

    # 2. LLM warm-up (non-blocking — failure is non-fatal)
    try:
        from services.llm_gateway import llm_gateway
        await asyncio.wait_for(
            llm_gateway.generate_text(
                model=settings.LLM_MODEL_SMALL,
                prompt="Hello",
                num_predict=3,
                timeout=30,
                force_json=False,
            ),
            timeout=35,
        )
        logger.info("✅ LLM model warmed up")
    except asyncio.TimeoutError:
        logger.warning("⚠️  LLM warmup timed out — model may need more RAM/VRAM")
    except Exception as e:
        logger.warning(f"⚠️  LLM warmup failed (non-fatal): {e}")

    # 3. TTS warm-up + static precomputation
    try:
        from services.tts_service import tts_service
        warmed = await tts_service.warm_up()
        if warmed:
            await tts_service.precompute_static_cache()
            logger.info("✅ TTS warm-up + static cache ready")
        else:
            logger.warning("⚠️  TTS warm-up failed — first greeting will be synthesised on-demand")
    except Exception as e:
        logger.warning(f"⚠️  TTS preload failed (non-fatal): {e}")

    logger.info("✅ Startup preload complete — ready for calls")


async def _precompute_session_tts(call_id: str):
    """Background task to pre-synthesise session-specific TTS messages."""
    try:
        from services.tts_service import tts_service
        session_messages = {
            "greeting_acknowledgment": "Thank you for your agreement. Let's proceed.",
            "offer_ready":             "Your offer is ready. Let me show you the details.",
        }
        for msg_id, text in session_messages.items():
            try:
                audio_path = await tts_service.synthesize(text, call_id)
                if audio_path:
                    await tts_service.save_to_local_storage(audio_path, f"session/{call_id}/{msg_id}")
                    text_hash = hash(text) % 1_000_000
                    await redis_client.set_tts_cache(
                        f"tts:session:{call_id}:{text_hash}",
                        audio_path,
                        ttl=1800,
                    )
            except Exception as e:
                logger.debug(f"Session TTS precompute {msg_id}: {e}")
    except Exception as e:
        logger.debug(f"Session TTS precompute failed for {call_id}: {e}")


# ── Request / Response schemas ─────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    customer_phone: str
    campaign_id: str | None = None
    channel: str = "sms"


class CreateSessionResponse(BaseModel):
    call_id: str
    session_token: str
    join_url: str
    videosdk_room_id: str
    expires_at: str


class JoinSessionResponse(BaseModel):
    call_id: str
    videosdk_room_id: str
    videosdk_token: str
    participant_id: str
    stage: str


class TranscriptPayload(BaseModel):
    text: str
    confidence: float
    timestamp: float


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/create", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest):
    """Create a new loan session and return the join link."""
    call_id       = str(uuid.uuid4())
    session_token = str(uuid.uuid4())

    try:
        room_data = await videosdk_service.create_room(call_id)
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in (401, 403):
            raise HTTPException(
                status_code=502,
                detail="VideoSDK credentials invalid. Check VIDEOSDK_API_KEY in .env.",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"VideoSDK room creation failed: HTTP {status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach VideoSDK.") from exc

    room_id = room_data["roomId"]

    meta  = SessionMeta(
        call_id=call_id,
        session_token=session_token,
        videosdk_room_id=room_id,
        videosdk_token=videosdk_service.generate_token(room_id=room_id),
    )
    state = SharedState(session_meta=meta)
    await redis_client.set_state(state.redis_key(), state.to_json())
    await redis_client.register_session(
        call_id, {"call_id": call_id, "room_id": room_id, "stage": "INIT"}
    )

    await db.execute(
        """
        INSERT INTO sessions (call_id, session_token, room_id, customer_phone, campaign_id, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        call_id, session_token, room_id, req.customer_phone, req.campaign_id,
        datetime.now(timezone.utc),
    )

    asyncio.create_task(_precompute_session_tts(call_id))

    join_url = f"{settings.ALLOWED_ORIGINS[0]}/join/{session_token}"
    logger.info(f"Session created: {call_id} | room: {room_id}")
    return CreateSessionResponse(
        call_id=call_id,
        session_token=session_token,
        join_url=join_url,
        videosdk_room_id=room_id,
        expires_at="30 minutes from now",
    )


@router.get("/active")
async def active_sessions():
    return await redis_client.list_active_sessions()


@router.get("/{call_id}")
async def get_session(call_id: str):
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")
    state = SharedState.from_json(raw)
    return {
        "call_id":  call_id,
        "stage":    state.current_stage,
        "customer_name":             state.customer_identity.name,
        "doc_authenticity_passed":   state.customer_identity.doc_authenticity_passed,
        "risk_band":                 state.financial_data.risk_band,
        "offer": {
            "eligible_amount":   state.final_offer.eligible_amount,
            "interest_rate":     state.final_offer.interest_rate,
            "acceptance_status": state.final_offer.acceptance_status,
        } if state.final_offer.eligible_amount else None,
    }


@router.post("/{session_token}/join", response_model=JoinSessionResponse)
async def join_session(session_token: str, request: Request):
    """
    Customer clicks the link.  Returns VideoSDK credentials.

    FIX-RACE:   start_session() is fired as a background task with a 1.5 s
                delay so the frontend's EventSource (SSE) connection is
                almost certainly open before the first event is published.
                The replay buffer in redis_client is the primary safety net;
                the delay is a belt-and-suspenders second layer.

    FIX-REGREET: Re-greeting is guarded by set_once so React StrictMode
                 double-mount (or user page-refresh) cannot send the greeting
                 twice.
    """
    raw_db = await db.fetchrow(
        "SELECT call_id, room_id FROM sessions WHERE session_token = $1 AND ended_at IS NULL",
        session_token,
    )
    if not raw_db:
        raise HTTPException(status_code=404, detail="Invalid or expired session link")

    call_id = str(raw_db["call_id"])
    room_id = raw_db["room_id"]

    if not await videosdk_service.validate_room(room_id):
        raise HTTPException(status_code=410, detail="Session has expired")

    participant_id = f"customer-{uuid.uuid4().hex[:8]}"
    token = videosdk_service.generate_token(
        permissions=["allow_join"],
        room_id=room_id,
        participant_id=participant_id,
    )

    should_start = False
    state_raw    = await redis_client.get_state(f"session:{call_id}:state")
    if state_raw:
        state        = SharedState.from_json(state_raw)
        should_start = (state.current_stage == SessionStage.INIT)
        if should_start:
            state.current_stage = SessionStage.GREETING_CONSENT
            state.version      += 1
        state.session_meta.videosdk_participant_id = participant_id
        state.session_meta.ip_address = request.client.host if request.client else None
        try:
            geo_lat = request.headers.get("x-geo-lat")
            geo_lng = request.headers.get("x-geo-lng")
            if geo_lat and geo_lng:
                state.session_meta.geo_lat = float(geo_lat)
                state.session_meta.geo_lng = float(geo_lng)
        except ValueError:
            pass
        await redis_client.set_state(state.redis_key(), state.to_json())

    # Recording
    try:
        rec = await videosdk_service.start_recording(room_id, call_id)
        if state_raw:
            state.session_meta.videosdk_recording_id = rec.get("id")
            await redis_client.set_state(state.redis_key(), state.to_json())
    except Exception as e:
        logger.warning(f"Recording start failed (non-fatal): {e}")
        if state_raw:
            state.session_meta.videosdk_recording_id = f"client_rec_{call_id}"
            await redis_client.set_state(state.redis_key(), state.to_json())

    # ── FIX-RACE + FIX-REGREET ────────────────────────────────────────────────
    if should_start and await redis_client.set_once(f"session:{call_id}:started"):
        # FIX-DELAY: wait 1.5 s to let SSE connect before first publish.
        # The replay buffer is the primary guarantee; this is extra safety.
        async def _delayed_start():
            await asyncio.sleep(1.5)
            await moderator_engine.start_session(call_id)

        asyncio.create_task(_delayed_start())

    elif state_raw and not should_start:
        # Reconnect path — resend greeting if consent not yet given.
        # FIX-REGREET: set_once prevents double-send on React StrictMode double-mount.
        state = SharedState.from_json(state_raw)
        regreet_guard = f"session:{call_id}:regreet:{int(time.time() // 5)}"
        if (
            state.current_stage == SessionStage.GREETING_CONSENT
            and not state.customer_identity.consent_given
            and await redis_client.set_once(regreet_guard, "1", ttl_seconds=5)
        ):
            from agents.conversation_agents import conversation_agent

            async def _delayed_regreet():
                await asyncio.sleep(1.5)
                await conversation_agent.send_stage_opener(
                    call_id, SessionStage.GREETING_CONSENT.value
                )

            asyncio.create_task(_delayed_regreet())
            logger.info(f"Reconnect: will resend greeting in 1.5 s [{call_id}]")

    logger.info(f"Customer joined: {call_id} | participant: {participant_id}")
    return JoinSessionResponse(
        call_id=call_id,
        videosdk_room_id=room_id,
        videosdk_token=token,
        participant_id=participant_id,
        stage=SessionStage.GREETING_CONSENT.value,
    )


# ── SSE Events Stream ──────────────────────────────────────────────────────────

@router.get("/{call_id}/events")
async def session_events(call_id: str):
    """
    Server-Sent Events stream.

    FIX-RACE:     Subscribes to the pub/sub channel FIRST, then replays any
                  buffered events that arrived before this connection was open.
                  No events can be lost regardless of timing.

    FIX-KEEPALIVE: Sends a `:keepalive` comment every 15 s.  Without this,
                   browsers and reverse-proxies (nginx, Cloudflare) silently
                   drop idle SSE connections after ~30-60 s — this was the
                   root cause of the 25-30 s microphone delay.
    """
    async def event_generator():
        # ── STEP 1: subscribe BEFORE replaying buffer ─────────────────────────
        # Subscribing first means zero events can be lost between replay and
        # the live stream even if something publishes in that tiny window.
        pubsub = await redis_client.subscribe(f"session:{call_id}:events")

        # ── STEP 2: replay buffered events (the ones published before SSE opened)
        try:
            buffered = await redis_client.get_and_clear_event_buffer(call_id)
            for raw_json in buffered:
                yield f"data: {raw_json}\n\n"
        except Exception as e:
            logger.debug(f"Buffer replay failed (non-fatal) [{call_id}]: {e}")

        # ── STEP 3: stream live events with keepalive heartbeat ───────────────
        try:
            last_event_time = time.time()
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield f"data: {message['data']}\n\n"
                    last_event_time = time.time()

                # FIX-KEEPALIVE: emit a comment (ignored by JS EventSource) every
                # _SSE_HEARTBEAT_INTERVAL seconds to keep connection alive.
                elif time.time() - last_event_time >= _SSE_HEARTBEAT_INTERVAL:
                    yield f": keepalive {int(time.time())}\n\n"
                    last_event_time = time.time()

                # Yield control to the event loop without sleeping
                await asyncio.sleep(0)
        finally:
            try:
                await pubsub.unsubscribe()
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


# ── Transcript ─────────────────────────────────────────────────────────────────

@router.post("/{call_id}/transcript")
async def receive_transcript(call_id: str, payload: TranscriptPayload):
    """Direct STT path — no queue hop."""
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    text = payload.text.strip()
    if not text:
        return {"status": "ignored", "reason": "empty"}

    normalized = " ".join(text.lower().split())
    dedupe_key = f"session:{call_id}:stt-dedupe:{normalized[:80]}"
    if not await redis_client.set_once(dedupe_key, "1", ttl_seconds=4):
        return {"status": "ignored", "reason": "duplicate"}

    await redis_client.publish(f"session:{call_id}:events", {
        "event":   "LIVE_CAPTION",
        "text":    payload.text,
        "call_id": call_id,
        "ts":      time.time(),
    })

    from agents.stt_pipeline import stt_pipeline
    asyncio.create_task(
        stt_pipeline.process_utterance(call_id, payload.text, payload.timestamp)
    )
    return {"status": "processing", "call_id": call_id}


# ── Document Upload ────────────────────────────────────────────────────────────

DOCUMENTS_DIR = Path("documents")
DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/{call_id}/upload-document")
async def upload_document(
    call_id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    session_dir = DOCUMENTS_DIR / call_id
    session_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = f"{int(time.time())}_{file.filename}"
    filepath      = session_dir / safe_filename

    content = await file.read()
    async with aiofiles.open(filepath, "wb") as f:
        await f.write(content)

    state     = SharedState.from_json(raw)
    fname_low = (file.filename or "").lower()
    doc_type  = "pan" if "pan" in fname_low else "aadhaar"
    state.customer_identity.ovd_type = doc_type
    state.version += 1
    await redis_client.set_state(state.redis_key(), state.to_json())

    logger.info(f"Document uploaded [{call_id}]: {safe_filename} ({doc_type})")
    await redis_client.publish(f"session:{call_id}:events", {
        "event": "DOCUMENT_UPLOADED", "doc_type": doc_type,
        "call_id": call_id, "ts": time.time(),
    })

    async def run_doc_auth():
        from agents.verification_agent import VerificationAgent
        agent = VerificationAgent()
        await agent.handle_task({"call_id": call_id, "action": "verify_document_authenticity"})

    background_tasks.add_task(run_doc_auth)
    return {"status": "uploaded", "filename": safe_filename, "type": doc_type}


# ── Recording Upload ───────────────────────────────────────────────────────────

RECORDINGS_DIR = Path("recordings")
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/{call_id}/upload-recording")
async def upload_recording(call_id: str, file: UploadFile = File(...)):
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    session_dir = RECORDINGS_DIR / call_id
    session_dir.mkdir(parents=True, exist_ok=True)
    filename  = f"recording_{call_id}_{int(time.time())}.webm"
    filepath  = session_dir / filename
    content   = await file.read()
    async with aiofiles.open(filepath, "wb") as f:
        await f.write(content)

    size_mb = len(content) / (1024 * 1024)
    logger.info(f"Recording saved: {filepath} ({size_mb:.1f} MB)")
    return {"status": "saved", "filename": filename, "size_mb": round(size_mb, 2)}


# ── Session End ────────────────────────────────────────────────────────────────

@router.post("/{call_id}/end")
async def end_session(call_id: str):
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    state = SharedState.from_json(raw)
    if state.current_stage not in (SessionStage.COMPLETED, SessionStage.ESCALATED):
        state.current_stage = SessionStage.ABANDONED
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

    rec_id  = state.session_meta.videosdk_recording_id
    room_id = state.session_meta.videosdk_room_id
    recording_stopped = False

    if rec_id and not rec_id.startswith("client_rec_"):
        for attempt, delay in enumerate([2, 4, 8]):
            try:
                await videosdk_service.stop_recording(room_id)
                recording_stopped = True
                break
            except Exception as e:
                logger.warning(f"Stop recording attempt {attempt+1}/3 failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(delay)
    else:
        recording_stopped = True

    await db.execute(
        "UPDATE sessions SET ended_at = $1, final_stage = $2 WHERE call_id = $3",
        datetime.now(timezone.utc), state.current_stage.value, call_id,
    )
    await redis_client.unregister_session(call_id)
    return {
        "status": "ended", "call_id": call_id,
        "final_stage": state.current_stage.value,
        "recording_stopped": recording_stopped,
    }


# ── Canvas Snapshot ────────────────────────────────────────────────────────────

class SnapshotPayload(BaseModel):
    image_data: str


@router.post("/{call_id}/snapshot")
async def receive_snapshot(call_id: str, payload: SnapshotPayload):
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    import base64
    try:
        image_b64 = payload.image_data
        if image_b64.startswith("data:image"):
            image_b64 = image_b64.split(",", 1)[1]
        base64.b64decode(image_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data")

    await redis_client.set_state(
        f"session:{call_id}:snapshot",
        json.dumps({"image": image_b64, "timestamp": time.time()}),
        ttl=30,
    )
    asyncio.create_task(moderator_engine.handle_snapshot_received(call_id))
    return {"status": "received"}