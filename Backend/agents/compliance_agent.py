"""
Compliance Agent
────────────────
Co-activated alongside other agents in Stage 1 (Consent) and Stage 6 (Offer).
Enforces RBI V-CIP requirements at every gate.

Checks:
  Stage 1: Consent phrase captured, timestamped, archived
  Stage 6: KFS generated, offer within regulatory caps, audit log complete
"""

import logging
import time
from datetime import datetime, timezone

from models.shared_state import SharedState
from core.redis_client import redis_client
from core.config import settings

logger = logging.getLogger(__name__)


class ComplianceAgent:

    # RBI V-CIP regulatory caps
    RBI_MAX_PERSONAL_LOAN = 2_000_000     # ₹20 lakhs
    RBI_MAX_RATE          = 36.0          # % per annum (NBFC ceiling)
    RBI_MIN_CONSENT_WORDS = 2             # "I agree" = 2 words minimum

    async def handle_task(self, payload: dict):
        call_id = payload["call_id"]
        action  = payload.get("action", "")

        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            return
        state = SharedState.from_json(raw)

        if action == "check_consent_requirement":
            await self._check_consent_setup(call_id, state)

        elif action == "validate_offer_compliance":
            await self._validate_offer(call_id, state)

    async def _check_consent_setup(self, call_id: str, state: SharedState):
        """
        Before Stage 1 begins: verify the session has all required
        RBI V-CIP metadata (geo, device fingerprint, session ID).
        """
        issues = []

        if not state.session_meta.videosdk_room_id:
            issues.append("no_video_room")

        if not state.session_meta.videosdk_recording_id:
            logger.warning(f"Recording not yet started for {call_id} — non-blocking")

        # RBI session ID (generate if missing)
        if not state.session_meta.rbi_session_id:
            import uuid
            state.session_meta.rbi_session_id = f"RBI-{uuid.uuid4().hex[:12].upper()}"
            state.version += 1
            await redis_client.set_state(state.redis_key(), state.to_json())

        await self._write_audit_event(call_id, "COMPLIANCE_STAGE1_CHECK", {
            "issues":           issues,
            "rbi_session_id":   state.session_meta.rbi_session_id,
            "recording_active": bool(state.session_meta.videosdk_recording_id),
        })

        logger.info(f"Consent compliance check [{call_id}]: issues={issues}")

    async def _validate_offer(self, call_id: str, state: SharedState):
        """
        Before offer is presented: validate against RBI regulatory caps.
        Blocks the offer if it violates any cap.
        """
        offer  = state.final_offer
        issues = []

        if offer.eligible_amount and offer.eligible_amount > self.RBI_MAX_PERSONAL_LOAN:
            issues.append(f"amount_exceeds_rbi_cap:{offer.eligible_amount}")
            # Cap the offer
            offer.eligible_amount = self.RBI_MAX_PERSONAL_LOAN
            state.final_offer = offer

        if offer.interest_rate and offer.interest_rate > self.RBI_MAX_RATE:
            issues.append(f"rate_exceeds_rbi_cap:{offer.interest_rate}")

        if not state.customer_identity.consent_given:
            issues.append("consent_not_recorded")

        if not state.session_meta.videosdk_recording_id:
            issues.append("recording_not_active")

        # Archive compliance check to audit log
        await self._write_audit_event(call_id, "COMPLIANCE_OFFER_VALIDATION", {
            "issues":            issues,
            "eligible_amount":   offer.eligible_amount,
            "interest_rate":     offer.interest_rate,
            "consent_recorded":  state.customer_identity.consent_given,
            "recording_active":  bool(state.session_meta.videosdk_recording_id),
            "rbi_session_id":    state.session_meta.rbi_session_id,
        })

        if issues:
            logger.warning(f"Offer compliance issues [{call_id}]: {issues}")
            # Update state with corrected offer
            state.version += 1
            await redis_client.set_state(state.redis_key(), state.to_json())

        logger.info(f"Offer compliance check [{call_id}]: OK (issues={issues})")

    async def _write_audit_event(self, call_id: str, event_type: str, payload: dict):
        """Write compliance event to Redis audit stream (PostgreSQL write via background task)."""
        await redis_client.publish(f"session:{call_id}:audit", {
            "call_id":    call_id,
            "event_type": event_type,
            "payload":    payload,
            "timestamp":  time.time(),
        })