"""
LangGraph Moderator Engine (Async High-Speed Version)
───────────────────────────────────────────────────────
Drop-in replacement for the original LangGraph DAG.
Provides the same public API but uses an in-process async state machine.

Architecture change:
  - Removed RabbitMQ for agent activation (using EventBus/Direct calls).
  - Removed Liveness Challenge stage for increased speed.
  - Achieved sub-2s stage transitions.
"""

import asyncio
import logging
import time

from models.shared_state import SharedState, SessionStage, STAGE_SEQUENCE, ModeratorLogEntry
from core.redis_client import redis_client
from core.event_bus import event_bus, Events

logger = logging.getLogger(__name__)

# ── Stage gate definitions ─────────────────────────────────────────────────────

def _consent_gate(state: SharedState) -> bool:
    return state.customer_identity.consent_given

def _ovd_gate(state: SharedState) -> bool:
    return bool(state.customer_identity.ovd_type)

def _identity_gate(state: SharedState) -> bool:
    return bool(state.customer_identity.name)

def _income_gate(state: SharedState) -> bool:
    return bool(state.financial_data.monthly_income)

def _purpose_gate(state: SharedState) -> bool:
    return bool(state.extracted_signals.loan_purpose)

def _risk_gate(state: SharedState) -> bool:
    from models.shared_state import RiskBand
    return state.financial_data.risk_band != RiskBand.UNKNOWN

def _offer_gate(state: SharedState) -> bool:
    return state.final_offer.acceptance_status in ("ACCEPTED", "DECLINED_INELIGIBLE", "ELIGIBILITY_PENDING_DOCS")

STAGE_GATES = {
    SessionStage.GREETING_CONSENT:     _consent_gate,
    SessionStage.OVD_DOCUMENT_CAPTURE: _ovd_gate,
    SessionStage.IDENTITY_KYC:         _identity_gate,
    SessionStage.EMPLOYMENT_INCOME:    _income_gate,
    SessionStage.LOAN_PURPOSE:         _purpose_gate,
    SessionStage.RISK_ASSESSMENT:      _risk_gate,
    SessionStage.OFFER_ACCEPTANCE:     _offer_gate,
}

class ModeratorEngine:
    """
    Event-driven stage machine. Stateless — all state lives in Redis.
    One instance shared across all sessions (singleton).
    """

    async def start_session(self, call_id: str):
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            logger.error(f"start_session: no state for {call_id}")
            return

        state = SharedState.from_json(raw)

        # The /join route pre-sets stage to GREETING_CONSENT before this runs
        # (via asyncio.sleep(1.5) delay). Accept both INIT and GREETING_CONSENT
        # as valid starting states.
        if state.current_stage == SessionStage.INIT:
            await self._enter_stage(call_id, SessionStage.GREETING_CONSENT)
        elif state.current_stage == SessionStage.GREETING_CONSENT:
            # Stage already set by /join — just fire the STAGE_ENTERED event
            # so ConversationAgent sends the greeting TTS.
            logger.info(f"start_session: stage already GREETING_CONSENT, firing event [{call_id}]")
            await event_bus.emit(Events.STAGE_ENTERED, {"call_id": call_id, "stage": SessionStage.GREETING_CONSENT.value})
        else:
            logger.info(f"start_session: already past greeting ({state.current_stage}) for {call_id}")

    async def advance_stage(self, call_id: str, result: dict):
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            return
        state = SharedState.from_json(raw)

        agent      = result.get("agent", "unknown")
        passed     = result.get("passed", False)
        escalate   = result.get("escalate", False)
        confidence = result.get("confidence", 0.0)
        issues     = result.get("issues", [])

        state.moderator_log.append(ModeratorLogEntry(
            stage=state.current_stage.value,
            agent_activated=agent,
            action_taken="advance_stage",
            confidence=confidence,
            escalated_to_human=escalate,
        ))

        if escalate:
            await self._escalate(call_id, state, reason=", ".join(issues) or "agent_requested")
            return

        if result.get("ineligible"):
            passed = True

        if not passed:
            state.stage_retry_count += 1
            if state.stage_retry_count > state.max_retries_per_stage:
                await self._escalate(call_id, state, reason="max_retries_exceeded")
                return

            state.version += 1
            await redis_client.set_state(state.redis_key(), state.to_json())
            await self._re_ask(call_id, state)
            return

        gate = STAGE_GATES.get(state.current_stage)
        if gate and not gate(state):
            logger.debug(f"Gate not satisfied for {state.current_stage} [{call_id}]")
            state.stage_retry_count += 1
            state.version += 1
            await redis_client.set_state(state.redis_key(), state.to_json())
            await self._re_ask(call_id, state)
            return

        next_stage = state.next_stage()
        if next_stage is None:
            await self._complete(call_id, state)
            return

        state.stage_retry_count = 0
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())
        await self._enter_stage(call_id, next_stage)

    async def handle_stt_processed(self, call_id: str, confidence: float, consent_present: bool, stage: str):
        if confidence < 0.5:
            await self._publish_re_ask(call_id, "low_stt_confidence")
            return

        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw: return
        state = SharedState.from_json(raw)

        if stage == SessionStage.GREETING_CONSENT.value and consent_present:
            if state.current_stage == SessionStage.GREETING_CONSENT:
                await self.advance_stage(call_id, {"passed": True, "agent": "stt_pipeline", "confidence": confidence})
                return

        gate = STAGE_GATES.get(state.current_stage)
        if gate and gate(state):
            await self.advance_stage(call_id, {"passed": True, "agent": "stt_pipeline", "confidence": confidence})

    async def handle_snapshot_received(self, call_id: str):
        # Vision is now non-blocking and high-speed
        asyncio.create_task(self._run_vision_async(call_id))

    async def _enter_stage(self, call_id: str, stage: SessionStage):
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw: return
        state = SharedState.from_json(raw)

        state.current_stage = stage
        state.stage_retry_count = 0
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        await redis_client.publish(f"session:{call_id}:events", {
            "event": "STAGE_CHANGED",
            "stage": stage.value,
            "call_id": call_id,
            "ts": time.time(),
        })

        await event_bus.emit(Events.STAGE_ENTERED, {"call_id": call_id, "stage": stage.value})

        # ── Agent Activation Side Effects ────────────────────────────────────
        if stage == SessionStage.RISK_ASSESSMENT:
            asyncio.create_task(self._run_risk_assessment(call_id))
        elif stage == SessionStage.OFFER_ACCEPTANCE:
            asyncio.create_task(self._run_offer_generation(call_id))

    async def _re_ask(self, call_id: str, state: SharedState):
        reason_map = {
            SessionStage.GREETING_CONSENT:     "missing_consent",
            SessionStage.OVD_DOCUMENT_CAPTURE: "missing_ovd",
            SessionStage.IDENTITY_KYC:         "missing_name",
            SessionStage.EMPLOYMENT_INCOME:    "missing_income",
            SessionStage.LOAN_PURPOSE:         "ambiguous_purpose",
        }
        reason = reason_map.get(state.current_stage, "low_stt_confidence")
        await self._publish_re_ask(call_id, reason)

    async def _publish_re_ask(self, call_id: str, reason: str):
        await redis_client.publish(f"session:{call_id}:events", {
            "event": "RE_ASK",
            "reason": reason,
            "call_id": call_id,
        })
        await event_bus.emit(Events.LOW_CONFIDENCE_SPEECH, {"call_id": call_id, "reason": reason})

    async def _escalate(self, call_id: str, state: SharedState, reason: str = ""):
        state.current_stage = SessionStage.ESCALATED
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        await redis_client.publish(f"session:{call_id}:events", {
            "event": "SESSION_ESCALATED",
            "reason": reason,
            "call_id": call_id,
        })
        
        await event_bus.emit(Events.SESSION_ESCALATED, {"call_id": call_id, "reason": reason})
        logger.warning(f"Session escalated [{call_id}]: {reason}")

    async def _complete(self, call_id: str, state: SharedState):
        state.current_stage = SessionStage.COMPLETED
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        await redis_client.publish(f"session:{call_id}:events", {
            "event": "SESSION_COMPLETED",
            "call_id": call_id,
        })
        await event_bus.emit(Events.SESSION_COMPLETED, {"call_id": call_id})

    async def _run_risk_assessment(self, call_id: str):
        try:
            from agents.risk_agent import RiskAgent
            agent = RiskAgent()
            await agent.handle_task({"call_id": call_id, "action": "full_risk_assessment"})
        except Exception as e:
            logger.error(f"Risk assessment failed: {e}")
            await self.advance_stage(call_id, {"passed": True, "agent": "risk_fallback"})

    async def _run_offer_generation(self, call_id: str):
        try:
            from agents.offer_agent import OfferAgent
            agent = OfferAgent()
            await agent.handle_task({"call_id": call_id, "action": "generate_offer"})
        except Exception as e:
            logger.error(f"Offer generation failed: {e}")

    async def _run_vision_async(self, call_id: str):
        try:
            from agents.vision_agent import VisionAgent
            agent = VisionAgent()
            # Changed from liveness to face match for speed
            await agent.handle_task({"call_id": call_id, "action": "run_face_match_age_check"})
        except Exception as e:
            logger.debug(f"Vision agent skipped: {e}")

moderator_engine = ModeratorEngine()
