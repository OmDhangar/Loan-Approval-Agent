"""
VideoSDK Service
────────────────
Wraps all VideoSDK REST API calls and JWT generation.
Replaces raw Mediasoup/WebRTC server plumbing while keeping
the same SFrame E2E encryption guarantees via VideoSDK's
custom encryption hooks.

VideoSDK docs: https://docs.videosdk.live/
"""

import time
import uuid
import logging
import httpx
import jwt

from core.config import settings

logger = logging.getLogger(__name__)


class VideoSDKService:
    """
    Responsibilities
    ----------------
    1. Generate short-lived VideoSDK JWT tokens per participant
    2. Create / end meeting rooms (one room = one loan session)
    3. Start / stop cloud recordings (linked to RBI audit)
    4. Fetch real-time network quality stats per participant
    5. Add AI-agent as a silent participant for Whisper audio capture
    """

    BASE = settings.VIDEOSDK_API_ENDPOINT

    # ── Token generation ──────────────────────────────────────────────────────

    def generate_token(
        self,
        permissions: list[str] = None,
        expiry_minutes: int = None,
        room_id: str | None = None,
        participant_id: str | None = None,
    ) -> str:
        """
        Sign a VideoSDK JWT.
        permissions: ["allow_join"] | ["allow_join","allow_mod"]
        """
        if permissions is None:
            permissions = ["allow_join"]
        if expiry_minutes is None:
            expiry_minutes = settings.VIDEOSDK_TOKEN_EXPIRY_MINUTES

        now = int(time.time())
        payload: dict = {
            "apikey": settings.VIDEOSDK_API_KEY,
            "permissions": permissions,
            "iat": now,
            "exp": now + (expiry_minutes * 60),
            "version": 2,
        }
        if room_id:
            payload["roomId"] = room_id
        if participant_id:
            payload["participantId"] = participant_id

        token = jwt.encode(
            payload,
            settings.VIDEOSDK_SECRET_KEY,
            algorithm="HS256",
        )
        return token if isinstance(token, str) else token.decode()

    # ── Room management ───────────────────────────────────────────────────────

    async def create_room(self, call_id: str) -> dict:
        """
        Create a VideoSDK room.
        Returns: { roomId, links: { get_room, get_session } }
        """
        token = self.generate_token(permissions=["allow_join", "allow_mod"])
        payload = {
            "customRoomId": f"lw-{call_id}",   # deterministic from call_id
            "autoCloseConfig": {
                "type": "session-end-and-deactivate",
                "duration": 30,                 # close 30s after last participant leaves
            },
        }

        webhook_endpoint = f"{settings.ALLOWED_ORIGINS[0]}/api/v1/webhook/videosdk"
        if webhook_endpoint.startswith("https://"):
            payload["webhook"] = {
                "endPoint": webhook_endpoint,
                "events": [
                    "session-started",
                    "session-ended",
                    "participant-joined",
                    "participant-left",
                    "recording-started",
                    "recording-stopped",
                ],
            }
        else:
            logger.info("Skipping VideoSDK webhook registration for non-HTTPS endpoint: %s", webhook_endpoint)

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self.BASE}/rooms",
                headers={"Authorization": token, "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"VideoSDK room created: {data.get('roomId')} for call {call_id}")
            return data

    async def validate_room(self, room_id: str) -> bool:
        """Check if a room is still active."""
        token = self.generate_token()
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{self.BASE}/rooms/validate/{room_id}",
                headers={"Authorization": token},
            )
            return resp.status_code == 200

    # ── Recording (RBI audit trail) ───────────────────────────────────────────

    async def start_recording(self, room_id: str, session_id: str) -> dict:
        """
        Start cloud recording. VideoSDK stores to its CDN;
        we then pull the recording URL and archive to S3 Mumbai.

        config.layout.type = SPOTLIGHT keeps only the customer's video large
        (not the agent overlay) for clean KYC audit footage.
        """
        token = self.generate_token(permissions=["allow_join", "allow_mod"])
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self.BASE}/recordings/start",
                headers={"Authorization": token, "Content-Type": "application/json"},
                json={
                    "roomId": room_id,
                    "config": {
                        "layout": {
                            "type": "SPOTLIGHT",
                            "priority": "PIN",          # PIN = customer always in spotlight
                            "gridSize": 1,
                        },
                        "theme": "DARK",
                        "mode": "video-and-audio",
                        "quality": "high",
                        "orientation": "portrait",
                    },
                    "storageConfig": {                  # Optional: direct-to-S3
                        "type": "s3",
                        "bucket": settings.S3_BUCKET_RECORDINGS,
                        "region": settings.AWS_REGION,
                        "prefix": f"sessions/{session_id}/",
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Recording started for room {room_id}: {data}")
            return data

    async def stop_recording(self, room_id: str) -> dict:
        """Stop cloud recording for the given room."""
        token = self.generate_token(permissions=["allow_join", "allow_mod"])
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self.BASE}/recordings/end",
                headers={"Authorization": token, "Content-Type": "application/json"},
                json={"roomId": room_id},
            )
            resp.raise_for_status()
            return resp.json()

    # ── Live Transcription (feeds Whisper pipeline) ───────────────────────────

    async def start_transcription(self, room_id: str, webhook_url: str) -> dict:
        """
        Start VideoSDK's real-time transcription.
        Each utterance is POST-ed to our webhook where Whisper re-processes
        for higher accuracy and entity extraction.
        """
        token = self.generate_token(permissions=["allow_join", "allow_mod"])
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self.BASE}/transcriptions/start",
                headers={"Authorization": token, "Content-Type": "application/json"},
                json={
                    "roomId": room_id,
                    "webhookUrl": webhook_url,
                    "summary": {"enabled": False},     # We do our own summarisation
                },
            )
            resp.raise_for_status()
            return resp.json()

    # ── Real-time quality stats ───────────────────────────────────────────────

    async def get_participant_quality(self, room_id: str, participant_id: str) -> dict:
        """
        Returns network quality score (1-5) and bandwidth stats.
        Used by Moderator to trigger audio-first fallback.
        """
        token = self.generate_token()
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{self.BASE}/rooms/{room_id}/participants/{participant_id}/quality",
                headers={"Authorization": token},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"score": 3, "bandwidth": None}   # safe default

    # ── Human oversight participant ───────────────────────────────────────────

    def generate_oversight_token(self, room_id: str, official_id: str) -> str:
        """
        Generate a moderator-permission token for the RBI oversight official.
        This token allows them to join the existing room in listener/takeover mode.
        """
        return self.generate_token(
            permissions=["allow_join", "allow_mod"],
            room_id=room_id,
            participant_id=f"official-{official_id}",
            expiry_minutes=120,
        )

    # ── AI Agent silent participant ────────────────────────────────────────────

    def generate_agent_token(self, room_id: str) -> str:
        """
        Token for the backend AI agent that joins as a silent participant
        to receive audio frames for Whisper STT processing.
        """
        return self.generate_token(
            permissions=["allow_join"],
            room_id=room_id,
            participant_id=f"ai-agent-{uuid.uuid4().hex[:8]}",
            expiry_minutes=120,
        )


# ── Singleton ─────────────────────────────────────────────────────────────────
videosdk_service = VideoSDKService()
