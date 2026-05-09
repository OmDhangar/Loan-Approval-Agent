"""
STT Pipeline
─────────────
Direct async processor for speech transcripts.

Refactor changes:
  - Removed RabbitMQ dependency — called directly from session.py transcript endpoint
  - Removed LIVENESS_CHALLENGE stage handling
  - Emits EventBus events instead of pushing to moderator via interrupt
  - process_utterance() is now a direct async call, not a queue consumer
"""

import logging
import re
import time
from typing import Optional

from models.shared_state import SharedState, ConversationEntry, SessionStage
from core.redis_client import redis_client
from core.event_bus import event_bus, Events
from core.langgraph_engine import moderator_engine

logger = logging.getLogger(__name__)


class STTPipeline:
    """
    Direct-call STT pipeline with entity extraction.
    Called from the /transcript endpoint synchronously — no queue hop.
    """

    async def process_utterance(self, call_id: str, raw_text: str, timestamp: float):
        """
        Main STT processing pipeline:
        1. Accept transcript from browser Web Speech API
        2. Compute confidence heuristic
        3. Extract entities with local LLM (stage-aware)
        4. Apply entities to SharedState (with validation)
        5. Write updated state to Redis
        6. Emit live caption to frontend via Redis pub/sub
        7. Signal SessionOrchestrator via EventBus
        """
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            logger.warning(f"STTPipeline: no state for {call_id}")
            return

        state = SharedState.from_json(raw)
        transcript = raw_text.strip()
        if not transcript:
            return

        confidence = self._estimate_confidence(transcript)
        entities   = await self._extract_entities(transcript, state.current_stage.value)
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

        # Live caption to frontend
        await redis_client.publish(f"session:{call_id}:events", {
            "event":      "STT_UTTERANCE",
            "transcript": transcript,
            "confidence": confidence,
            "entities":   entities,
            "call_id":    call_id,
            "ts":         time.time(),
        })

        # Let the orchestrator decide on stage progression
        await moderator_engine.handle_stt_processed(
            call_id=call_id,
            confidence=confidence,
            consent_present=state.customer_identity.consent_given,
            stage=state.current_stage.value,
        )

    # ── Confidence estimation ──────────────────────────────────────────────────

    def _estimate_confidence(self, transcript: str) -> float:
        """
        Heuristic confidence for MVP.
        Browser Web Speech API confidence is often 0, so we use text quality.
        """
        if not transcript or len(transcript.strip()) < 2:
            return 0.3
        words = transcript.strip().split()
        if len(words) < 2:
            return 0.6     # Single word — possibly misheard
        return 0.95

    # ── Entity extraction ──────────────────────────────────────────────────────

    async def _extract_entities(self, transcript: str, stage: str) -> dict:
        """
        Stage-aware entity extraction via local LLM (Ollama).
        Falls back to keyword matching for critical fields (consent, OTP, OVD type).
        """
        from services.llm_gateway import llm_gateway
        from core.config import settings

        stage_hints = {
            "GREETING_CONSENT":      "Look for: consent phrases like 'I agree', 'yes I consent', 'haan'",
            "OVD_DOCUMENT_CAPTURE":  "Look for: document type (aadhaar, PAN), any document number",
            "IDENTITY_KYC":          "Look for: full name (first + last), date of birth (DD/MM/YYYY)",
            "EMPLOYMENT_INCOME":     "Look for: employment type (salaried/self-employed), monthly income in rupees",
            "LOAN_PURPOSE":          "Look for: loan purpose, amount needed, repayment period in months",
            "OFFER_ACCEPTANCE":      "Look for: acceptance or rejection, selected tenure",
        }
        hint = stage_hints.get(stage, "Extract any relevant financial or identity information")

        prompt = f"""Extract structured data from this customer speech transcript.
Stage: {stage}
Hint: {hint}
Transcript: "{transcript}"

Respond ONLY with valid JSON. Example:
{{"name": null, "dob": null, "income": null, "employment_type": null, "consent": null, "loan_purpose": null, "loan_amount": null, "ovd_type": null}}

If a field is not mentioned, use null. Extract ONLY what is clearly stated."""

        entities = await llm_gateway.generate_structured(
            model=settings.LLM_MODEL_SMALL,
            prompt=prompt,
            required_keys=["name", "consent"],
            num_predict=80,
            timeout=6,   # Shorter timeout for real-time feel
        )

        # If LLM returned nothing (timeout/unavailable), use keyword-only extraction
        if not entities:
            logger.info(f"LLM unavailable for STT — using keyword-only extraction (stage={stage})")
            entities = self._keyword_extract(transcript, stage)

        # ── Keyword fallbacks for critical fields ──────────────────────────

        # Consent
        if stage == "GREETING_CONSENT":
            consent_val = str(entities.get("consent", "") or "").lower()
            if consent_val not in ("yes", "true", "i agree", "agree"):
                text = transcript.lower()
                if any(w in text for w in ["agree", "consent", "yes", "i do", "haan", "thik hai", "ok", "okay"]):
                    entities["consent"] = "yes"

        # OVD type
        if stage == "OVD_DOCUMENT_CAPTURE":
            if not entities.get("ovd_type"):
                text = transcript.lower()
                if any(w in text for w in ["aadhaar", "aadhar", "adhar", "uid"]):
                    entities["ovd_type"] = "aadhaar"
                elif any(w in text for w in ["pan", "pan card", "income tax"]):
                    entities["ovd_type"] = "pan"



        # Offer acceptance
        if stage == "OFFER_ACCEPTANCE":
            if not entities.get("accepted"):
                text = transcript.lower()
                if any(w in text for w in ["accept", "yes", "agree", "proceed", "haan", "theek"]):
                    entities["accepted"] = "yes"
                elif any(w in text for w in ["reject", "no", "decline", "nahi", "nope"]):
                    entities["accepted"] = "no"

        return entities

    # ── Entity application ─────────────────────────────────────────────────────

    def _apply_entities(self, state: SharedState, entities: dict):
        """Write extracted entities to SharedState with validation."""

        # Name — must be 2+ words, alphabetic, 5+ chars
        if entities.get("name"):
            name = str(entities["name"]).strip()
            words = name.split()
            if (
                len(words) >= 2
                and len(name) >= 5
                and all(re.match(r"^[A-Za-z\.'-]+$", w) for w in words)
            ):
                state.customer_identity.name = name
            else:
                logger.warning(f"Rejected invalid name: '{name}'")

        if entities.get("dob"):
            state.customer_identity.declared_dob = str(entities["dob"])

        # Income — ₹1,000 to ₹1 crore sanity range
        if entities.get("income"):
            try:
                income = float(str(entities["income"]).replace(",", "").replace("₹", ""))
                if 1_000 <= income <= 10_000_000:
                    state.financial_data.monthly_income = income
                else:
                    logger.warning(f"Income out of range: {income}")
            except (ValueError, TypeError):
                pass

        # Employment type
        if entities.get("employment_type"):
            emp = str(entities["employment_type"]).strip().lower()
            valid = {"salaried", "self-employed", "self employed", "business", "freelance", "professional"}
            if emp in valid:
                state.financial_data.employment_type = entities["employment_type"]

        if entities.get("loan_purpose"):
            state.extracted_signals.loan_purpose = str(entities["loan_purpose"])

        if entities.get("loan_amount"):
            try:
                amount = float(str(entities["loan_amount"]).replace(",", "").replace("₹", ""))
                if amount > 0:
                    state.extracted_signals.loan_amount_requested = amount
            except (ValueError, TypeError):
                pass

        # Consent
        if str(entities.get("consent", "") or "").lower() in ("yes", "true", "i agree", "agree"):
            state.customer_identity.consent_given = True
            state.customer_identity.consent_timestamp = time.time()

        # OVD document type
        if entities.get("ovd_type"):
            ovd = str(entities["ovd_type"]).lower()
            if ovd in ("aadhaar", "pan", "passport"):
                state.customer_identity.ovd_type = ovd

        # Offer acceptance
        if str(entities.get("accepted", "") or "").lower() == "yes":
            if state.final_offer.eligible_amount:
                state.final_offer.acceptance_status = "ACCEPTED"
        elif str(entities.get("accepted", "") or "").lower() == "no":
            if state.final_offer.eligible_amount:
                state.final_offer.acceptance_status = "DECLINED"

    # ── Keyword-only extraction fallback ─────────────────────────────────────

    def _keyword_extract(self, transcript: str, stage: str) -> dict:
        """
        Pure keyword/regex extraction when LLM is unavailable.
        Covers the critical fields needed to advance each stage.
        """
        text = transcript.lower().strip()
        entities = {}

        # Consent
        if stage == "GREETING_CONSENT":
            if any(w in text for w in ["agree", "consent", "yes", "i do", "haan", "thik hai", "ok", "okay"]):
                entities["consent"] = "yes"

        # OVD type
        if stage == "OVD_DOCUMENT_CAPTURE":
            if any(w in text for w in ["aadhaar", "aadhar", "adhar", "uid"]):
                entities["ovd_type"] = "aadhaar"
            elif any(w in text for w in ["pan", "pan card", "income tax"]):
                entities["ovd_type"] = "pan"

        # Name (simple: 2+ capitalized words)
        if stage == "IDENTITY_KYC":
            words = transcript.strip().split()
            name_words = [w for w in words if w[0:1].isupper() and len(w) >= 2 and w.isalpha()]
            if len(name_words) >= 2:
                entities["name"] = " ".join(name_words[:3])
            # DOB pattern
            dob_match = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', transcript)
            if dob_match:
                entities["dob"] = dob_match.group(0)

        # Income
        if stage == "EMPLOYMENT_INCOME":
            income_match = re.search(r'(\d[\d,]*)', transcript.replace(' ', ''))
            if income_match:
                try:
                    val = float(income_match.group(1).replace(',', ''))
                    if 1_000 <= val <= 10_000_000:
                        entities["income"] = val
                except ValueError:
                    pass
            if any(w in text for w in ["salaried", "salary"]):
                entities["employment_type"] = "salaried"
            elif any(w in text for w in ["self-employed", "self employed", "business", "freelance"]):
                entities["employment_type"] = "self-employed"

        # Loan purpose
        if stage == "LOAN_PURPOSE":
            purposes = {
                "home": "home_renovation", "renovation": "home_renovation",
                "education": "education", "study": "education",
                "medical": "medical", "health": "medical", "hospital": "medical",
                "business": "business", "wedding": "wedding", "marriage": "wedding",
                "car": "vehicle", "vehicle": "vehicle", "bike": "vehicle",
                "personal": "personal", "travel": "travel",
            }
            for keyword, purpose in purposes.items():
                if keyword in text:
                    entities["loan_purpose"] = purpose
                    break
            amount_match = re.search(r'(\d[\d,]*)', transcript.replace(' ', ''))
            if amount_match:
                try:
                    val = float(amount_match.group(1).replace(',', ''))
                    if val > 0:
                        entities["loan_amount"] = val
                except ValueError:
                    pass

        # Offer acceptance
        if stage == "OFFER_ACCEPTANCE":
            if any(w in text for w in ["accept", "yes", "agree", "proceed", "haan", "theek"]):
                entities["accepted"] = "yes"
            elif any(w in text for w in ["reject", "no", "decline", "nahi", "nope"]):
                entities["accepted"] = "no"

        return entities


# ── Singleton ─────────────────────────────────────────────────────────────────
stt_pipeline = STTPipeline()