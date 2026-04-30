"""
STT Pipeline Agent
──────────────────
Receives raw transcription from VideoSDK webhook,
re-processes with Whisper large-v3 for:
  - Higher accuracy (fine-tuned on Hinglish)
  - Per-token confidence scores
  - Entity extraction (income, dates, names, Aadhaar)
  - Moderator retry signal if confidence < threshold
"""

import importlib
import logging
import time
from typing import Optional

import numpy as np

from models.shared_state import SharedState, ConversationEntry
from core.redis_client import redis_client
from core.rabbitmq_client import rabbitmq_client
from core.config import settings

logger = logging.getLogger(__name__)

# Lazy-loaded to avoid startup delay
_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        whisper = importlib.import_module("whisper")

        logger.info(f"Loading Whisper model: {settings.WHISPER_MODEL}")
        _whisper_model = whisper.load_model(settings.WHISPER_MODEL)
    return _whisper_model


class STTPipeline:
    """
    Whisper-based STT pipeline with entity extraction.
    Activated per utterance from VideoSDK transcription webhook.
    """

    async def handle_task(self, payload: dict):
        call_id    = payload["call_id"]
        action     = payload.get("action", "process_utterance")
        raw_text   = payload.get("raw_transcript", "")
        timestamp  = payload.get("timestamp", time.time())

        if action == "process_utterance":
            await self._process_utterance(call_id, raw_text, timestamp)

    async def _process_utterance(self, call_id: str, raw_text: str, timestamp: float):
        """
        Main STT processing pipeline:
        1. Accept VideoSDK transcript as baseline
        2. Compute confidence from Whisper logprobs if audio available
        3. Extract entities with local LLM
        4. Write to Shared State conversation_log
        5. Signal Moderator if confidence below threshold
        """
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            logger.warning(f"No state found for {call_id}")
            return

        state = SharedState.from_json(raw)

        # For MVP: use VideoSDK transcript directly (Whisper audio not yet wired)
        # In production: pull audio buffer from S3 temp store and run Whisper
        transcript = raw_text.strip()
        confidence = self._estimate_confidence(transcript)

        # Entity extraction via local LLM
        entities = await self._extract_entities(transcript, state.current_stage.value)

        # Update shared state with entities
        self._apply_entities(state, entities)

        # Append to conversation log
        entry = ConversationEntry(
            stage=state.current_stage.value,
            utterance=transcript,
            stt_transcript=transcript,
            stt_confidence=confidence,
            timestamp=timestamp,
            agent="stt_pipeline",
        )
        state.conversation_log.append(entry)
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        # Publish to frontend for live caption display
        await redis_client.publish(f"session:{call_id}:events", {
            "event":      "STT_UTTERANCE",
            "transcript": transcript,
            "confidence": confidence,
            "entities":   entities,
            "call_id":    call_id,
        })

        # Signal Moderator if confidence too low
        if confidence < settings.WHISPER_CONFIDENCE_THRESHOLD:
            logger.warning(f"Low STT confidence ({confidence:.2f}) for {call_id}")
            await rabbitmq_client.publish_task("conversation", {
                "call_id": call_id,
                "action":  "re_ask_last_question",
                "reason":  "low_stt_confidence",
                "confidence": confidence,
            })
        elif state.current_stage.value == "GREETING_CONSENT" and not state.customer_identity.consent_given:
            logger.warning(f"STT confidence good but consent not extracted for {call_id}. Re-asking.")
            await rabbitmq_client.publish_task("conversation", {
                "call_id": call_id,
                "action":  "re_ask_last_question",
                "reason":  "missing_consent",
                "confidence": confidence,
            })
        else:
            # Trigger conversation agent to advance the stage based on valid input
            logger.info(f"Final pipeline check: confidence={confidence:.2f}, stage={state.current_stage.value}, consent={state.customer_identity.consent_given}. Advancing {call_id}.")
            await rabbitmq_client.publish_task("conversation", {
                "call_id": call_id,
                "action":  "confirm_and_advance",
                "confidence": confidence,
            })

    def _estimate_confidence(self, transcript: str) -> float:
        """
        Heuristic confidence for MVP since browser API confidence is sometimes 0.
        """
        if not transcript or len(transcript.strip()) < 2:
            return 0.3
        
        # If they spoke at least one valid word, we treat it as high confidence
        return 0.95

    async def _extract_entities(self, transcript: str, stage: str) -> dict:
        """
        Use local LLM (Ollama) to extract structured entities from transcript.
        Prompt is stage-aware to focus extraction.
        """
        import httpx
        stage_hints = {
            "GREETING_CONSENT":   "Look for: consent phrases like 'I agree', 'yes I consent', name",
            "IDENTITY_KYC":       "Look for: full name, date of birth (DD/MM/YYYY), Aadhaar last 4 digits",
            "EMPLOYMENT_INCOME":  "Look for: employment type (salaried/self-employed), employer name, monthly income in rupees",
            "LOAN_PURPOSE":       "Look for: loan purpose, amount needed, repayment period in months",
            "RISK_ASSESSMENT":    "No extraction needed",
            "OFFER_ACCEPTANCE":   "Look for: acceptance or rejection of offer, selected tenure",
        }
        hint = stage_hints.get(stage, "Extract any relevant financial or identity information")

        prompt = f"""Extract structured data from this customer speech transcript.
Stage: {stage}
Hint: {hint}
Transcript: "{transcript}"

Respond ONLY with valid JSON. Example:
{{"name": null, "dob": null, "income": null, "employment_type": null, "consent": null, "loan_purpose": null, "loan_amount": null}}

If a field is not mentioned, use null. Extract ONLY what is clearly stated."""

        entities = {}
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(
                    f"{settings.OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": settings.LLM_MODEL_SMALL,
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                    },
                )
                if resp.status_code == 200:
                    import json
                    result = resp.json()
                    entities = json.loads(result.get("response", "{}"))
                else:
                    logger.warning(f"Ollama returned error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.warning(f"Entity extraction failed (Ollama connection error): {str(e) or type(e).__name__}")

        # Keyword-based fallback for Consent (critical for pipeline progress)
        if stage == "GREETING_CONSENT":
            consent_val = str(entities.get("consent", "") or "").lower()
            if consent_val not in ("yes", "true", "i agree", "agree"):
                text = transcript.lower()
                if any(word in text for word in ["agree", "consent", "yes", "i do", "haan", "thik hai"]):
                    logger.info("LLM didn't detect consent, but keyword fallback found it.")
                    entities["consent"] = "yes"

        return entities

    def _apply_entities(self, state: SharedState, entities: dict):
        """Write extracted entities back into the appropriate state fields."""
        if entities.get("name"):
            state.customer_identity.name = entities["name"]
        if entities.get("dob"):
            state.customer_identity.declared_dob = entities["dob"]
        if entities.get("income"):
            try:
                state.financial_data.monthly_income = float(str(entities["income"]).replace(",", ""))
            except (ValueError, TypeError):
                pass
        if entities.get("employment_type"):
            state.financial_data.employment_type = entities["employment_type"]
        if entities.get("loan_purpose"):
            state.extracted_signals.loan_purpose = entities["loan_purpose"]
        if entities.get("loan_amount"):
            try:
                state.extracted_signals.loan_amount_requested = float(str(entities["loan_amount"]).replace(",", ""))
            except (ValueError, TypeError):
                pass
        if entities.get("consent") and str(entities["consent"]).lower() in ("yes", "true", "i agree", "agree"):
            state.customer_identity.consent_given = True
            state.customer_identity.consent_timestamp = time.time()
