"""
Conversation Agent
──────────────────
Activated in every stage by the Moderator.
Manages the actual dialogue with the customer using local LLM (Llama 3.1 8B).

Responsibilities:
  - Greet the customer and request RBI-compliant consent (Stage 1)
  - Ask structured questions for each stage
  - Handle re-asks when STT confidence is low
  - Detect intent and classify responses
  - Push response text to frontend via Redis pub/sub
    (frontend renders it as the AI agent's speech / text bubble)
"""

import logging
import time
from typing import Optional

import httpx

from models.shared_state import SharedState, SessionStage
from core.redis_client import redis_client
from core.rabbitmq_client import rabbitmq_client
from core.config import settings
from core.langgraph_engine import moderator_engine
from services.tts_service import tts_service

logger = logging.getLogger(__name__)


# ── Stage scripts ─────────────────────────────────────────────────────────────
# Deterministic opening question per stage (LLM used only for follow-ups)

STAGE_OPENERS = {
    SessionStage.GREETING_CONSENT: (
        "Hello! I'm your Loan Wizard AI assistant from Poonawalla Fincorp. "
        "This call is recorded for security and compliance as required by RBI. "
        "To continue, please say 'I agree' or 'I consent'. "
        "Do you consent to proceed with this loan application?"
    ),
    SessionStage.IDENTITY_KYC: (
        "Great, thank you for your consent. "
        "Could you please tell me your full name as it appears on your Aadhaar card?"
    ),
    SessionStage.EMPLOYMENT_INCOME: (
        "Thank you. Now, are you currently salaried or self-employed? "
        "And what is your approximate monthly income in rupees?"
    ),
    SessionStage.LOAN_PURPOSE: (
        "Understood. What is the purpose of the loan you're applying for today? "
        "For example — home renovation, education, medical, or business?"
    ),
    SessionStage.RISK_ASSESSMENT: None,   # No dialogue – automated
    SessionStage.OFFER_ACCEPTANCE: (
        "I'm now generating a personalised loan offer based on your profile. "
        "Please hold for just a moment…"
    ),
}

RE_ASK_TEMPLATES = {
    "low_stt_confidence": "I'm sorry, I didn't catch that clearly. Could you please repeat that?",
    "missing_income":     "Could you tell me your monthly income in rupees? For example, '35,000 rupees per month'.",
    "missing_name":       "Could you please clearly state your full name as on your Aadhaar?",
    "missing_consent":    "To proceed, I need your verbal consent. Please say 'I agree' or 'I consent'.",
    "ambiguous_purpose":  "Could you be more specific about the loan purpose? For example, home renovation, education, or medical expenses?",
}


class ConversationAgent:

    async def handle_task(self, payload: dict):
        call_id = payload["call_id"]
        action  = payload.get("action", "")

        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            logger.warning(f"ConversationAgent: No state for {call_id}")
            return

        state = SharedState.from_json(raw)

        if action == "greet_and_request_consent":
            await self._send_message(call_id, STAGE_OPENERS[SessionStage.GREETING_CONSENT])

        elif action == "collect_identity_info":
            await self._send_message(call_id, STAGE_OPENERS[SessionStage.IDENTITY_KYC])

        elif action == "collect_employment_income":
            await self._send_message(call_id, STAGE_OPENERS[SessionStage.EMPLOYMENT_INCOME])

        elif action == "collect_loan_purpose":
            await self._send_message(call_id, STAGE_OPENERS[SessionStage.LOAN_PURPOSE])

        elif action == "re_ask_last_question":
            reason   = payload.get("reason", "low_stt_confidence")
            template = RE_ASK_TEMPLATES.get(reason, RE_ASK_TEMPLATES["low_stt_confidence"])
            await self._send_message(call_id, template)

        elif action == "confirm_and_advance":
            msg = await self._generate_confirmation(state)
            await self._send_message(call_id, msg)
            # Notify Moderator that stage is complete
            await moderator_engine.advance_stage(call_id, {
                "passed":     True,
                "agent":      "conversation",
                "confidence": 0.9,
            })

        elif action == "generate_dynamic_response":
            # LLM-generated contextual follow-up
            msg = await self._generate_followup(state, payload.get("context", ""))
            await self._send_message(call_id, msg)

    async def _send_message(self, call_id: str, text: str):
        """
        Publish AI agent message to frontend via Redis pub/sub.
        Also synthesizes TTS audio asynchronously.
        """
        event_data = {
            "event":   "AI_AGENT_SPEECH",
            "text":    text,
            "call_id": call_id,
            "ts":      time.time(),
        }
        
        # Synthesize audio asynchronously without blocking message publish
        try:
            audio_path = await tts_service.synthesize(text, call_id)
            if audio_path:
                # Convert filesystem path to HTTP URL for frontend
                from pathlib import Path as _P
                audio_filename = _P(audio_path).name
                event_data["audio_url"] = f"/api/v1/session/tts/audio/{audio_filename}"
                logger.info(f"TTS synthesized for call {call_id}: {audio_filename}")
            else:
                logger.warning(f"TTS synthesis failed for call {call_id}, sending text-only")
        except Exception as e:
            logger.warning(f"TTS synthesis error for call {call_id}: {e}")
            # Continue with text-only message if TTS fails
        
        await redis_client.publish(f"session:{call_id}:events", event_data)
        logger.debug(f"Agent message sent [{call_id}]: {text[:60]}...")

    async def _generate_confirmation(self, state: SharedState) -> str:
        """Generate a brief stage-completion confirmation message."""
        stage_confirmations = {
            SessionStage.GREETING_CONSENT:  f"Thank you{', ' + state.customer_identity.name if state.customer_identity.name else ''}. Consent recorded.",
            SessionStage.IDENTITY_KYC:      f"Identity verified. Thank you{', ' + state.customer_identity.name if state.customer_identity.name else ''}.",
            SessionStage.EMPLOYMENT_INCOME: "Income details noted. Thank you.",
            SessionStage.LOAN_PURPOSE:      "Got it. Assessing your loan eligibility now.",
        }
        return stage_confirmations.get(
            state.current_stage,
            "Thank you. Moving to the next step."
        )

    async def _generate_followup(self, state: SharedState, context: str) -> str:
        """
        Use local LLM to generate a contextual follow-up question.
        Only triggered when the structured opener doesn't resolve the stage.
        """
        prompt = f"""You are a friendly, professional Indian bank loan officer on a video call.
Stage: {state.current_stage.value}
Customer name: {state.customer_identity.name or 'Customer'}
Context: {context}
Generate ONE short, clear follow-up question (max 2 sentences) to collect missing information.
Be warm, concise, and professional. Speak in simple English."""

        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(
                    f"{settings.OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model":  settings.LLM_MODEL_SMALL,
                        "prompt": prompt,
                        "stream": False,
                    },
                )
                if resp.status_code == 200:
                    return resp.json().get("response", "").strip()
        except Exception as e:
            logger.warning(f"LLM follow-up generation failed: {e}")

        return "Could you please clarify that for me?"

    async def check_consent_complete(self, state: SharedState) -> bool:
        """Check if consent stage is complete."""
        return (
            state.customer_identity.consent_given
            and state.customer_identity.consent_timestamp is not None
        )