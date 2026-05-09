"""
Conversation Agent
──────────────────
FIXES APPLIED:
  FIX-3  Static cache lookup now uses exact-text → cache-key mapping via
         STATIC_MESSAGES from tts_service instead of the broken
         "if cached_audio_path in text" check (file path vs spoken text).
  FIX-4  Step 3 (local FS fallback) now looks up the correct message_id
         for the current text instead of always checking greeting_initial.
  FIX-5  Cache-miss path now calls tts_service.save_to_session_cache()
         which writes the path back into Redis, making subsequent calls hits.
"""

import asyncio
import logging
import os
import time
from pathlib import Path as _P
from typing import Optional

from models.shared_state import SharedState, SessionStage
from core.redis_client import redis_client
from core.event_bus import event_bus, Events
from core.langgraph_engine import moderator_engine

logger = logging.getLogger(__name__)


# ── Stage openers ──────────────────────────────────────────────────────────────
# These strings must match STATIC_MESSAGES in tts_service.py EXACTLY for the
# static cache to hit.  Import the canonical map so there is only one source of
# truth and no risk of typo drift.

from services.tts_service import STATIC_MESSAGES, _TEXT_TO_CACHE_KEY

STAGE_OPENERS: dict[str, str] = {
    SessionStage.GREETING_CONSENT.value:     STATIC_MESSAGES["greeting_initial"],
    SessionStage.OVD_DOCUMENT_CAPTURE.value: STATIC_MESSAGES["ovd_request"],
    SessionStage.AADHAAR_VERIFICATION.value: STATIC_MESSAGES["otp_request"],
    SessionStage.IDENTITY_KYC.value: (
        "Almost there! Could you please confirm your full name as it appears "
        "on your Aadhaar card, and your date of birth? "
        "For example: 'My name is Rahul Sharma, born on 10th May 1994'."
    ),
    SessionStage.EMPLOYMENT_INCOME.value: (
        "Thank you. Are you currently salaried or self-employed? "
        "And what is your approximate monthly income in rupees? "
        "For example: 'I am salaried, earning 50,000 per month'."
    ),
    SessionStage.LOAN_PURPOSE.value: (
        "Understood. What is the purpose of the loan you're applying for today? "
        "For example — home renovation, education, medical expenses, or business? "
        "And how much amount are you looking for?"
    ),
    SessionStage.RISK_ASSESSMENT.value:  STATIC_MESSAGES["risk_assessment"],
    SessionStage.OFFER_ACCEPTANCE.value: STATIC_MESSAGES["offer_generation"],
}

RE_ASK_TEMPLATES: dict[str, str] = {
    "low_stt_confidence":  "I'm sorry, I didn't quite catch that. Could you please repeat that clearly?",
    "missing_income":      "Could you tell me your monthly income in rupees? For example, '35,000 rupees per month'.",
    "missing_name":        "I need your full name as on your Aadhaar — both first name and last name — and your date of birth.",
    "missing_consent":     "To proceed, I need your verbal consent. Please say 'I agree' or 'I consent'.",
    "ambiguous_purpose":   "Could you be more specific? For example: home renovation, education, or medical expenses?",
    "missing_ovd":         "Please use the upload button on your screen to share a clear photo of your Aadhaar or PAN card.",
    "missing_aadhaar_otp": "I need the OTP sent to your Aadhaar-linked mobile. Please read out the 6-digit number.",
}


class ConversationAgent:
    """
    Drives the customer-facing dialogue.
    Subscribes to EventBus events — no polling, no queue.
    """

    def __init__(self):
        event_bus.subscribe(Events.STAGE_ENTERED,        self._on_stage_entered)
        event_bus.subscribe(Events.LOW_CONFIDENCE_SPEECH, self._on_re_ask)

    # ── Event handlers ─────────────────────────────────────────────────────────

    async def _on_stage_entered(self, data: dict):
        call_id = data["call_id"]
        stage   = data["stage"]
        opener  = STAGE_OPENERS.get(stage)
        if opener:
            await self._send_message(call_id, opener)

    async def _on_re_ask(self, data: dict):
        call_id  = data["call_id"]
        reason   = data.get("reason", "low_stt_confidence")
        template = RE_ASK_TEMPLATES.get(reason, RE_ASK_TEMPLATES["low_stt_confidence"])
        await self._send_message(call_id, template)

    # ── Direct call API ────────────────────────────────────────────────────────

    async def send_stage_opener(self, call_id: str, stage: str):
        opener = STAGE_OPENERS.get(stage)
        if opener:
            logger.info(f"Sending stage opener for {stage} [{call_id}]: {opener[:50]}...")
            await self._send_message(call_id, opener)
        else:
            logger.warning(f"No stage opener found for stage: {stage}")

    async def send_confirmation(self, call_id: str, stage: SessionStage):
        confirmations = {
            SessionStage.GREETING_CONSENT:     "Thank you. Your consent has been recorded. Let's proceed with identity verification.",
            SessionStage.OVD_DOCUMENT_CAPTURE: "Your document has been received and verified. Let's complete your Aadhaar verification.",
            SessionStage.AADHAAR_VERIFICATION:  "Aadhaar verified successfully. Could you now confirm your identity details?",
            SessionStage.IDENTITY_KYC:          "Identity confirmed. Let me now collect your employment details.",
            SessionStage.EMPLOYMENT_INCOME:     "Income details noted. And what's the purpose of your loan today?",
            SessionStage.LOAN_PURPOSE:          "Got it. Let me now assess your loan eligibility.",
        }
        msg = confirmations.get(stage, "Thank you. Moving to the next step.")
        await self._send_message(call_id, msg)

    async def send_dynamic_response(self, call_id: str, context: str) -> str:
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            return "Could you please clarify that for me?"

        state = SharedState.from_json(raw)
        from services.llm_gateway import llm_gateway
        from core.config import settings

        prompt = (
            f"You are a friendly Indian bank loan officer on a video call.\n"
            f"Stage: {state.current_stage.value}\n"
            f"Customer name: {state.customer_identity.name or 'Customer'}\n"
            f"Context: {context}\n"
            f"Generate ONE short, clear follow-up question (max 2 sentences). "
            f"Be warm and professional. Simple English only."
        )
        text     = await llm_gateway.generate_text(
            model=settings.LLM_MODEL_SMALL, prompt=prompt,
            num_predict=50, timeout=20, force_json=False,
        )
        response = text or "Could you please clarify that for me?"
        await self._send_message(call_id, response)
        return response

    # ── Core message delivery ──────────────────────────────────────────────────

    async def _send_message(self, call_id: str, text: str):
        """
        Publish text to frontend immediately via SSE.
        TTS synthesis runs as fire-and-forget background task.
        """
        await redis_client.publish(f"session:{call_id}:events", {
            "event":   "AI_AGENT_SPEECH",
            "text":    text,
            "call_id": call_id,
            "ts":      time.time(),
        })
        logger.info(f"Agent speech published [{call_id}]: {text[:70]}...")
        asyncio.create_task(self._synthesize_and_deliver(call_id, text))

    async def _synthesize_and_deliver(self, call_id: str, text: str):
        """
        4-tier cache with correct lookup logic.

        Tier 1 — Redis session cache   (per-call, text-hash key)
        Tier 2 — Redis static cache    (FIX-3: exact-text → key via _TEXT_TO_CACHE_KEY)
        Tier 3 — Local FS static cache (FIX-4: correct message_id per text)
        Tier 4 — Synthesise fresh      (FIX-5: write-through to Redis on miss)
        """
        t_start = time.time()
        try:
            from services.tts_service import tts_service
            from pathlib import Path

            text_hash = hash(text) % 1_000_000

            # ── Tier 1: Redis session cache ────────────────────────────────
            session_key = f"tts:session:{call_id}:{text_hash}"
            cached_path = await redis_client.get_tts_cache(session_key)
            if cached_path and Path(cached_path).exists():
                elapsed = time.time() - t_start
                logger.info(f"TTS Tier-1 hit (session) [{call_id}] elapsed={elapsed:.3f}s")
                await self._publish_audio(call_id, cached_path, text, "session_cache")
                return

            # ── Tier 2: Redis static cache (FIX-3) ────────────────────────
            # _TEXT_TO_CACHE_KEY maps exact spoken text → "tts:static:<id>"
            static_redis_key = _TEXT_TO_CACHE_KEY.get(text)
            if static_redis_key:
                cached_path = await redis_client.get_tts_cache(static_redis_key)
                if cached_path and Path(cached_path).exists():
                    elapsed = time.time() - t_start
                    logger.info(f"TTS Tier-2 hit (static Redis) [{call_id}] elapsed={elapsed:.3f}s")
                    await self._publish_audio(call_id, cached_path, text, "static_cache")
                    return

            # ── Tier 3: Local FS static cache (FIX-4) ─────────────────────
            # Determine the correct message_id for this text, not always 'greeting_initial'
            message_id = None
            for mid, msg_text in STATIC_MESSAGES.items():
                if msg_text == text:
                    message_id = mid
                    break

            if message_id:
                local_path = await tts_service.retrieve_from_local_storage(
                    f"static/{message_id}"
                )
                if local_path and Path(local_path).exists():
                    elapsed = time.time() - t_start
                    logger.info(f"TTS Tier-3 hit (local FS: {message_id}) [{call_id}] elapsed={elapsed:.3f}s")
                    await self._publish_audio(call_id, local_path, text, "local_cache")
                    # Repopulate Redis so next call hits Tier 2
                    if static_redis_key:
                        await redis_client.set_tts_cache(static_redis_key, local_path, ttl=None)
                    return

            # ── Tier 4: Synthesise fresh (FIX-5: write-through) ───────────
            logger.info(f"TTS Tier-4 synthesise [{call_id}] — starting fresh synthesis")
            t_synth = time.time()
            audio_path = await tts_service.synthesize(text, call_id)
            synth_elapsed = time.time() - t_synth
            if audio_path and Path(audio_path).exists():
                # FIX-5: write-through — next call for the same text will be a Tier-1 hit
                await tts_service.save_to_session_cache(audio_path, call_id, text_hash)
                total_elapsed = time.time() - t_start
                logger.info(
                    f"TTS Tier-4 complete [{call_id}] synth={synth_elapsed:.2f}s total={total_elapsed:.2f}s"
                )
                await self._publish_audio(call_id, audio_path, text, "synthesised")
            else:
                logger.warning(f"TTS synthesis returned nothing [{call_id}] — frontend uses browser TTS")

        except Exception as e:
            elapsed = time.time() - t_start
            logger.warning(f"TTS pipeline error [{call_id}] elapsed={elapsed:.2f}s: {e}")
            # Frontend falls back to browser TTS automatically

    async def _publish_audio(
        self, call_id: str, audio_path: str, text: str, source: str
    ):
        """Publish TTS_AUDIO_READY with a URL the existing route can serve."""
        filename  = _P(audio_path).name
        audio_url = f"/api/v1/session/tts/audio/{filename}"
        logger.info(f"Publishing TTS audio [{call_id}] source={source} file={filename} url={audio_url}")
        await redis_client.publish(f"session:{call_id}:events", {
            "event":     "TTS_AUDIO_READY",
            "audio_url": audio_url,
            "text":      text,
            "call_id":   call_id,
            "ts":        time.time(),
            "source":    source,
        })
        logger.info(f"TTS audio published successfully [{call_id}]")


# ── Singleton ─────────────────────────────────────────────────────────────────
conversation_agent = ConversationAgent()