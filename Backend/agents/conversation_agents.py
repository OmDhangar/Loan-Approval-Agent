"""
Conversation Agent
──────────────────
Activated in every stage by the Moderator.
Manages the actual dialogue with the customer using local LLM (Llama 3.1 8B).

Responsibilities:
  - Greet the customer and request RBI-compliant consent (Stage 1)
  - Guide through V-CIP stages (OVD, Liveness, Aadhaar)
  - Ask structured questions for each stage
  - Handle re-asks when STT confidence is low
  - Detect intent and classify responses
  - Push response text to frontend via Redis pub/sub
    (frontend renders it as the AI agent's speech / text bubble)
"""

import logging
import time
import random
from typing import Optional

from models.shared_state import SharedState, SessionStage
from core.redis_client import redis_client
from core.rabbitmq_client import rabbitmq_client
from core.config import settings
from core.langgraph_engine import moderator_engine
from services.tts_service import tts_service
from services.llm_gateway import llm_gateway

logger = logging.getLogger(__name__)


# ── Stage scripts ─────────────────────────────────────────────────────────────
# Deterministic opening question per stage (LLM used only for follow-ups)

STAGE_OPENERS = {
    SessionStage.GREETING_CONSENT: (
        "Hello! I'm your Loan Wizard AI assistant from Poonawalla Fincorp. "
        "This call is being recorded for security and compliance as required by RBI. "
        "Your personal data will be processed solely for this loan application "
        "and handled in accordance with our privacy policy. "
        "To continue, please say 'I agree' or 'I consent'. "
    ),
    SessionStage.OVD_DOCUMENT_CAPTURE: (
        "Thank you for your consent. Now I need to verify your identity. "
        "Please upload your Aadhaar card or PAN card using the upload button on your screen. "
        "Make sure the image is clear and readable."
    ),
    SessionStage.LIVENESS_CHALLENGE: (
        "Great, I can see the document. Now for a quick liveness check to ensure you are "
        "physically present. Please look directly at the camera and blink your eyes "
        "twice slowly. This prevents any spoofing attempts."
    ),
    SessionStage.AADHAAR_VERIFICATION: (
        "Excellent! Now I'll verify your Aadhaar. An OTP has been sent to your "
        "Aadhaar-linked mobile number. Please read out the 6-digit OTP when you receive it. "
        "For this demo, you can say any 6-digit number like '1 2 3 4 5 6'."
    ),
    SessionStage.IDENTITY_KYC: (
        "Thank you for completing the verification steps. "
        "Now, could you please tell me your full name as it appears on your Aadhaar card, "
        "and your date of birth? For example: 'My name is Rahul Sharma, "
        "born on 10th May 1994'."
    ),
    SessionStage.EMPLOYMENT_INCOME: (
        "Thank you. Now, are you currently salaried or self-employed? "
        "And what is your approximate monthly income in rupees? "
        "For example: 'I am salaried, earning 50,000 per month'."
    ),
    SessionStage.LOAN_PURPOSE: (
        "Understood. What is the purpose of the loan you're applying for today? "
        "For example — home renovation, education, medical, or business? "
        "And how much amount are you looking for?"
    ),
    SessionStage.RISK_ASSESSMENT: None,   # No dialogue – automated
    SessionStage.OFFER_ACCEPTANCE: (
        "I'm now generating a personalised loan offer based on your profile. "
        "Please hold for just a moment…"
    ),
}

RE_ASK_TEMPLATES = {
    "low_stt_confidence":  "I'm sorry, I didn't catch that clearly. Could you please repeat that?",
    "missing_income":      "Could you tell me your monthly income in rupees? For example, '35,000 rupees per month'.",
    "missing_name":        "I need both your first name and last name as they appear on your Aadhaar. Could you please state your full name and date of birth?",
    "missing_consent":     "To proceed, I need your verbal consent. Please say 'I agree' or 'I consent'.",
    "ambiguous_purpose":   "Could you be more specific about the loan purpose? For example, home renovation, education, or medical expenses?",
    "missing_ovd":         "I need to verify your identity document. Please use the upload button on your screen to upload a clear image of your Aadhaar or PAN card.",
    "missing_liveness":    "I need you to complete the liveness check. Please look at the camera and blink your eyes twice slowly.",
    "missing_aadhaar_otp": "I need the OTP for Aadhaar verification. Please read out the 6-digit OTP sent to your Aadhaar-linked mobile. For this demo, you can say any 6-digit number.",
}

# ── Liveness challenge prompts (randomised for anti-spoofing) ─────────────────
LIVENESS_PROMPTS = [
    "Please blink your eyes twice slowly while looking at the camera.",
    "Please slowly turn your head to the left and then to the right.",
    "Please nod your head up and down slowly.",
]


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

        elif action == "request_ovd_document":
            await self._send_message(call_id, STAGE_OPENERS[SessionStage.OVD_DOCUMENT_CAPTURE])

        elif action == "run_liveness_challenge":
            # Pick a random liveness prompt for anti-spoofing
            challenge = random.choice(LIVENESS_PROMPTS)
            # Store which challenge was given
            state.customer_identity.liveness_challenge_type = "blink"
            state.version += 1
            await redis_client.set_state(state.redis_key(), state.to_json())
            await self._send_message(call_id, challenge)

        elif action == "request_aadhaar_otp":
            await self._send_message(call_id, STAGE_OPENERS[SessionStage.AADHAAR_VERIFICATION])

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
        name = state.customer_identity.name

        stage_confirmations = {
            SessionStage.GREETING_CONSENT: (
                f"Thank you{', ' + name if name else ''}. "
                "Your consent has been recorded. Let's proceed with identity verification."
            ),
            SessionStage.OVD_DOCUMENT_CAPTURE: (
                "Thank you, I've captured your document. "
                "Now let's do a quick liveness check."
            ),
            SessionStage.LIVENESS_CHALLENGE: (
                "Liveness check completed successfully. "
                "Now let's verify your Aadhaar."
            ),
            SessionStage.AADHAAR_VERIFICATION: (
                "Aadhaar verified successfully. "
                "Now I'll confirm your identity details."
            ),
            SessionStage.IDENTITY_KYC: (
                f"Identity verified{' for ' + name if name else ''}. "
                "Your name and date of birth have been confirmed against our records. "
                "Thank you."
            ),
            SessionStage.EMPLOYMENT_INCOME: "Income details noted. Thank you.",
            SessionStage.LOAN_PURPOSE: "Got it. Assessing your loan eligibility now.",
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

        text = await llm_gateway.generate_text(
            model=settings.LLM_MODEL_SMALL,
            prompt=prompt,
            num_predict=50,
            timeout=8,
            force_json=False,
        )
        if text:
            return text

        return "Could you please clarify that for me?"

    async def check_consent_complete(self, state: SharedState) -> bool:
        """Check if consent stage is complete."""
        return (
            state.customer_identity.consent_given
            and state.customer_identity.consent_timestamp is not None
        )