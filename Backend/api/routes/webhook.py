"""
VideoSDK Webhook Routes
────────────────────────
VideoSDK posts events to these endpoints:
  - session-started / session-ended
  - participant-joined / participant-left
  - recording-started / recording-stopped
  - transcription utterance (feeds our Whisper pipeline)
  - network quality updates
"""

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from core.config import settings
from core.redis_client import redis_client
from core.rabbitmq_client import rabbitmq_client
from models.shared_state import SharedState

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Signature verification ────────────────────────────────────────────────────

def _verify_videosdk_signature(body: bytes, signature: str) -> bool:
    """HMAC-SHA256 signature verification for VideoSDK webhooks."""
    expected = hmac.new(
        settings.VIDEOSDK_SECRET_KEY.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Main webhook handler ──────────────────────────────────────────────────────

@router.post("/videosdk")
async def videosdk_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Central VideoSDK webhook dispatcher.
    All events from VideoSDK land here.
    """
    body = await request.body()

    # Verify signature (skip in dev)
    if settings.APP_ENV != "development":
        sig = request.headers.get("videosdk-signature", "")
        if not _verify_videosdk_signature(body, sig):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(body)
    event   = payload.get("event")
    data    = payload.get("data", {})

    logger.info(f"VideoSDK webhook: {event}")

    # ── Dispatch by event type ────────────────────────────────────────────────

    if event == "session-started":
        background_tasks.add_task(_on_session_started, data)

    elif event == "session-ended":
        background_tasks.add_task(_on_session_ended, data)

    elif event == "participant-joined":
        background_tasks.add_task(_on_participant_joined, data)

    elif event == "participant-left":
        background_tasks.add_task(_on_participant_left, data)

    elif event == "recording-started":
        background_tasks.add_task(_on_recording_started, data)

    elif event == "recording-stopped":
        background_tasks.add_task(_on_recording_stopped, data)

    elif event == "transcription-utterance":
        # Real-time transcription from VideoSDK → queue for Whisper re-processing
        background_tasks.add_task(_on_transcription_utterance, data)

    elif event == "network-quality":
        background_tasks.add_task(_on_network_quality, data)

    return {"status": "received"}


# ── Event handlers ────────────────────────────────────────────────────────────

async def _on_session_started(data: dict):
    room_id = data.get("roomId", "")
    call_id = room_id.replace("lw-", "")   # Our naming convention: lw-{call_id}
    logger.info(f"VideoSDK session started: {call_id}")

    await redis_client.publish(f"session:{call_id}:events", {
        "event": "VIDEOSDK_SESSION_STARTED",
        "call_id": call_id,
        "timestamp": time.time(),
    })


async def _on_session_ended(data: dict):
    room_id = data.get("roomId", "")
    call_id = room_id.replace("lw-", "")
    logger.info(f"VideoSDK session ended: {call_id}")

    await redis_client.publish(f"session:{call_id}:events", {
        "event": "VIDEOSDK_SESSION_ENDED",
        "call_id": call_id,
        "timestamp": time.time(),
    })


async def _on_participant_joined(data: dict):
    room_id        = data.get("roomId", "")
    participant_id = data.get("participantId", "")
    call_id        = room_id.replace("lw-", "")

    logger.info(f"Participant joined: {participant_id} in {call_id}")

    raw = await redis_client.get_state(f"session:{call_id}:state")
    if raw:
        state = SharedState.from_json(raw)
        state.session_meta.videosdk_participant_id = participant_id
        await redis_client.set_state(state.redis_key(), state.to_json())

    await redis_client.publish(f"session:{call_id}:events", {
        "event": "PARTICIPANT_JOINED",
        "participant_id": participant_id,
        "call_id": call_id,
    })


async def _on_participant_left(data: dict):
    room_id        = data.get("roomId", "")
    participant_id = data.get("participantId", "")
    call_id        = room_id.replace("lw-", "")

    # If customer left before completion → mark as abandoned
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if raw:
        state = SharedState.from_json(raw)
        from models.shared_state import SessionStage
        if state.current_stage not in (SessionStage.COMPLETED, SessionStage.ESCALATED):
            logger.warning(f"Customer left mid-session: {call_id}")
            await redis_client.publish(f"session:{call_id}:events", {
                "event": "CUSTOMER_LEFT_EARLY",
                "call_id": call_id,
                "stage": state.current_stage,
            })


async def _on_recording_started(data: dict):
    room_id    = data.get("roomId", "")
    call_id    = room_id.replace("lw-", "")
    rec_id     = data.get("id", "")
    logger.info(f"Recording started: {rec_id} for {call_id}")

    raw = await redis_client.get_state(f"session:{call_id}:state")
    if raw:
        state = SharedState.from_json(raw)
        state.session_meta.videosdk_recording_id = rec_id
        await redis_client.set_state(state.redis_key(), state.to_json())


async def _on_recording_stopped(data: dict):
    room_id    = data.get("roomId", "")
    call_id    = room_id.replace("lw-", "")
    rec_url    = data.get("fileUrl", "")
    logger.info(f"Recording stopped for {call_id}. URL: {rec_url}")

    # Archive URL to PostgreSQL audit record
    # (actual S3 copy handled by a separate background job)
    await redis_client.publish(f"session:{call_id}:events", {
        "event": "RECORDING_COMPLETE",
        "recording_url": rec_url,
        "call_id": call_id,
    })


async def _on_transcription_utterance(data: dict):
    """
    VideoSDK real-time transcription arrives here.
    We re-queue to Whisper pipeline for:
      1. Higher accuracy (Whisper large-v3 > VideoSDK default)
      2. Entity extraction (income, age, dates)
      3. Confidence scoring → Moderator retry logic
    """
    room_id    = data.get("roomId", "")
    call_id    = room_id.replace("lw-", "")
    text       = data.get("text", "")
    confidence = float(data.get("confidence", 0.8))
    timestamp  = data.get("timestamp", time.time())

    await rabbitmq_client.publish_task("stt_pipeline", {
        "call_id":        call_id,
        "raw_transcript": text,
        "videosdk_confidence": confidence,
        "timestamp":      timestamp,
        "action":         "process_utterance",
    })


async def _on_network_quality(data: dict):
    """
    VideoSDK reports per-participant network quality (1=poor → 5=excellent).
    We cache this and the Moderator uses it to trigger audio-first fallback.
    """
    room_id    = data.get("roomId", "")
    call_id    = room_id.replace("lw-", "")
    score      = int(data.get("score", 3))
    participant = data.get("participantId", "")

    await redis_client.cache_quality_score(call_id, score)

    # If quality drops below 2, notify Moderator → trigger audio-first mode
    if score <= 2:
        logger.warning(f"Low network quality ({score}) for {call_id}")
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if raw:
            state = SharedState.from_json(raw)
            state.session_meta.network_quality_score = score
            await redis_client.set_state(state.redis_key(), state.to_json())

        await redis_client.publish(f"session:{call_id}:events", {
            "event":   "NETWORK_QUALITY_LOW",
            "score":   score,
            "call_id": call_id,
        })