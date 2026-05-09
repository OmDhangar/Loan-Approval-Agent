"""
VideoSDK Webhook Routes
────────────────────────
VideoSDK posts events to these endpoints.

Refactor changes:
  - transcription-utterance now calls stt_pipeline DIRECTLY (no RabbitMQ queue hop)
  - Removed liveness-related session stage references
  - Human escalation and audit log still use RabbitMQ (appropriate for non-RT work)
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from core.config import settings
from core.redis_client import redis_client
from models.shared_state import SharedState, SessionStage

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Signature verification ────────────────────────────────────────────────────

def _verify_videosdk_signature(body: bytes, signature: str) -> bool:
    expected = hmac.new(
        settings.VIDEOSDK_SECRET_KEY.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Main webhook handler ──────────────────────────────────────────────────────

@router.post("/videosdk")
async def videosdk_webhook(request: Request, background_tasks: BackgroundTasks):
    """Central VideoSDK webhook dispatcher."""
    body = await request.body()

    if settings.APP_ENV != "development":
        sig = request.headers.get("videosdk-signature", "")
        if not _verify_videosdk_signature(body, sig):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(body)
    event   = payload.get("event")
    data    = payload.get("data", {})

    logger.info(f"VideoSDK webhook: {event}")

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
        # DIRECT call — no RabbitMQ. Latency matters here.
        background_tasks.add_task(_on_transcription_utterance_direct, data)
    elif event == "network-quality":
        background_tasks.add_task(_on_network_quality, data)

    return {"status": "received"}


# ── Event handlers ────────────────────────────────────────────────────────────

async def _on_session_started(data: dict):
    room_id = data.get("roomId", "")
    call_id = room_id.replace("lw-", "")
    await redis_client.publish(f"session:{call_id}:events", {
        "event": "VIDEOSDK_SESSION_STARTED",
        "call_id": call_id,
        "ts": time.time(),
    })


async def _on_session_ended(data: dict):
    room_id = data.get("roomId", "")
    call_id = room_id.replace("lw-", "")
    await redis_client.publish(f"session:{call_id}:events", {
        "event": "VIDEOSDK_SESSION_ENDED",
        "call_id": call_id,
        "ts": time.time(),
    })


async def _on_participant_joined(data: dict):
    room_id        = data.get("roomId", "")
    participant_id = data.get("participantId", "")
    call_id        = room_id.replace("lw-", "")

    raw = await redis_client.get_state(f"session:{call_id}:state")
    if raw:
        state = SharedState.from_json(raw)
        state.session_meta.videosdk_participant_id = participant_id
        await redis_client.set_state(state.redis_key(), state.to_json())

    await redis_client.publish(f"session:{call_id}:events", {
        "event": "PARTICIPANT_JOINED",
        "participant_id": participant_id,
        "call_id": call_id,
        "ts": time.time(),
    })


async def _on_participant_left(data: dict):
    room_id        = data.get("roomId", "")
    participant_id = data.get("participantId", "")
    call_id        = room_id.replace("lw-", "")

    raw = await redis_client.get_state(f"session:{call_id}:state")
    if raw:
        state = SharedState.from_json(raw)
        if state.current_stage not in (SessionStage.COMPLETED, SessionStage.ESCALATED):
            logger.warning(f"Customer left mid-session: {call_id} at {state.current_stage}")
            await redis_client.publish(f"session:{call_id}:events", {
                "event": "CUSTOMER_LEFT_EARLY",
                "call_id": call_id,
                "stage": state.current_stage.value,
                "ts": time.time(),
            })


async def _on_recording_started(data: dict):
    room_id = data.get("roomId", "")
    call_id = room_id.replace("lw-", "")
    rec_id  = data.get("id", "")

    raw = await redis_client.get_state(f"session:{call_id}:state")
    if raw:
        state = SharedState.from_json(raw)
        state.session_meta.videosdk_recording_id = rec_id
        await redis_client.set_state(state.redis_key(), state.to_json())

    logger.info(f"Recording started: {rec_id} for {call_id}")


async def _on_recording_stopped(data: dict):
    room_id = data.get("roomId", "")
    call_id = room_id.replace("lw-", "")
    rec_url = data.get("fileUrl", "")

    await redis_client.publish(f"session:{call_id}:events", {
        "event": "RECORDING_COMPLETE",
        "recording_url": rec_url,
        "call_id": call_id,
        "ts": time.time(),
    })

    # No RabbitMQ needed; audit logging can be handled by EventBus or direct DB write
    logger.info(f"Recording complete for {call_id}: {rec_url}")


async def _on_transcription_utterance_direct(data: dict):
    """
    VideoSDK real-time transcription — processed DIRECTLY without queue.

    OLD flow: VideoSDK → RabbitMQ queue → STT consumer → Redis → Moderator (interrupt)
    NEW flow: VideoSDK → direct async call → Redis → EventBus → Orchestrator

    Eliminates ~100-500ms queue serialization overhead per utterance.
    """
    room_id   = data.get("roomId", "")
    call_id   = room_id.replace("lw-", "")
    text      = data.get("text", "").strip()
    timestamp = data.get("timestamp", time.time())

    if not text:
        return

    # Short-window deduplication (same as /transcript endpoint)
    normalized = " ".join(text.lower().split())
    dedupe_key = f"session:{call_id}:stt-dedupe:{normalized[:80]}"
    if not await redis_client.set_once(dedupe_key, "1", ttl_seconds=4):
        return

    # Direct call — no queue, no serialization
    from agents.stt_pipeline import stt_pipeline
    await stt_pipeline.process_utterance(call_id, text, timestamp)


async def _on_network_quality(data: dict):
    """Cache network quality; low score triggers audio-first fallback."""
    room_id     = data.get("roomId", "")
    call_id     = room_id.replace("lw-", "")
    score       = int(data.get("score", 3))
    participant = data.get("participantId", "")

    await redis_client.cache_quality_score(call_id, score)

    if score <= 2:
        logger.warning(f"Low network quality (score={score}) for {call_id}")
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if raw:
            state = SharedState.from_json(raw)
            state.session_meta.network_quality_score = score
            await redis_client.set_state(state.redis_key(), state.to_json())

        await redis_client.publish(f"session:{call_id}:events", {
            "event":   "NETWORK_QUALITY_LOW",
            "score":   score,
            "call_id": call_id,
            "ts":      time.time(),
        })