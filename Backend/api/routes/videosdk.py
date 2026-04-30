"""
VideoSDK Routes
────────────────
GET  /api/v1/videosdk/token         – Generate a fresh token (for reconnects)
POST /api/v1/videosdk/oversight     – Generate token for human oversight official
GET  /api/v1/videosdk/room/{id}     – Validate room is active
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from services.videosdk_service import videosdk_service
from core.redis_client import redis_client
from models.shared_state import SharedState

router = APIRouter()


class OversightTokenRequest(BaseModel):
    call_id:     str
    official_id: str


class OversightTokenResponse(BaseModel):
    token:   str
    room_id: str
    call_id: str


@router.get("/token")
async def get_token(room_id: str | None = None):
    """
    Generate a fresh VideoSDK JWT.
    Called by the frontend on reconnect (token expiry).
    """
    token = videosdk_service.generate_token(room_id=room_id)
    return {"token": token}


@router.post("/oversight", response_model=OversightTokenResponse)
async def get_oversight_token(req: OversightTokenRequest):
    """
    Generate a moderator-permission token for an RBI oversight official.
    Called by the Human Oversight queue worker when escalation is triggered.
    """
    raw = await redis_client.get_state(f"session:{req.call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    state   = SharedState.from_json(raw)
    room_id = state.session_meta.videosdk_room_id

    if not room_id:
        raise HTTPException(status_code=400, detail="No video room for this session")

    token = videosdk_service.generate_oversight_token(room_id, req.official_id)
    return OversightTokenResponse(
        token=token,
        room_id=room_id,
        call_id=req.call_id,
    )


@router.get("/room/{room_id}/validate")
async def validate_room(room_id: str):
    """Check if a VideoSDK room is still active."""
    active = await videosdk_service.validate_room(room_id)
    return {"room_id": room_id, "active": active}


@router.get("/room/{call_id}/quality")
async def get_network_quality(call_id: str):
    """Get cached network quality score for a session."""
    score = await redis_client.get_quality_score(call_id)
    return {
        "call_id":       call_id,
        "quality_score": score,
        "audio_first":   score <= 2,
    }