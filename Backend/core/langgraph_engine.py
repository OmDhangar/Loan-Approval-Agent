"""
LangGraph Moderator Engine
──────────────────────────
Implements the Supervisor-Worker Multi-Agent DAG.
Each node = one stage of the loan journey.
Conditional edges handle: advance / retry / escalate.

Stage flow (RBI V-CIP compliant):
  INIT → GREETING_CONSENT → OVD_DOCUMENT_CAPTURE → LIVENESS_CHALLENGE
       → AADHAAR_VERIFICATION → IDENTITY_KYC → EMPLOYMENT_INCOME
       → LOAN_PURPOSE → RISK_ASSESSMENT → OFFER_ACCEPTANCE → COMPLETED
"""

import logging
import time
import operator
from typing import Literal, TypedDict, Annotated, List, Dict

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command

from models.shared_state import SharedState, SessionStage, ModeratorLogEntry
from core.redis_client import redis_client
from core.rabbitmq_client import rabbitmq_client

logger = logging.getLogger(__name__)


# ── Type alias for LangGraph ──────────────────────────────────────────────────

StageDecision = Literal["advance", "retry", "escalate", "end"]

class GlobalGraphState(TypedDict):
    call_id: str
    stage_history: Annotated[List[Dict], operator.add]
    current_stage_result: Dict


# ── Required entities per stage ──────────────────────────────────────────────
# If the required entity is not present in state, the stage cannot advance.
STAGE_REQUIRED_ENTITIES = {
    "GREETING_CONSENT":     lambda s: s.customer_identity.consent_given,
    "OVD_DOCUMENT_CAPTURE": lambda s: s.customer_identity.ovd_type is not None,
    "LIVENESS_CHALLENGE":   lambda s: s.customer_identity.liveness_challenge_passed,
    "AADHAAR_VERIFICATION": lambda s: s.customer_identity.aadhaar_otp_verified,
    "IDENTITY_KYC":         lambda s: (
        s.customer_identity.name is not None
        and s.customer_identity.declared_dob is not None
    ),
    "EMPLOYMENT_INCOME":    lambda s: (
        s.financial_data.employment_type is not None
        and s.financial_data.monthly_income is not None
        and s.financial_data.monthly_income > 0
    ),
    "LOAN_PURPOSE":         lambda s: s.extracted_signals.loan_purpose is not None,
}

# ── Re-ask reasons per missing entity ────────────────────────────────────────
STAGE_REASK_REASON = {
    "GREETING_CONSENT":     "missing_consent",
    "OVD_DOCUMENT_CAPTURE": "missing_ovd",
    "LIVENESS_CHALLENGE":   "missing_liveness",
    "AADHAAR_VERIFICATION": "missing_aadhaar_otp",
    "IDENTITY_KYC":         "missing_name",
    "EMPLOYMENT_INCOME":    "missing_income",
    "LOAN_PURPOSE":         "ambiguous_purpose",
}


class ModeratorEngine:
    """
    Central LangGraph DAG controller.
    Called by the Moderator on every stage completion event.
    """

    def __init__(self):
        self._checkpointer = MemorySaver()
        self._graph = self._build_graph()
        self._conflict_count = 0
        self._retry_count = 0

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(GlobalGraphState)

        # ── Nodes (one per stage) ─────────────────────────────────────────────
        graph.add_node("greeting_consent",      self._node_greeting_consent)
        graph.add_node("ovd_document_capture",  self._node_ovd_document_capture)
        graph.add_node("liveness_challenge",    self._node_liveness_challenge)
        graph.add_node("aadhaar_verification",  self._node_aadhaar_verification)
        graph.add_node("identity_kyc",          self._node_identity_kyc)
        graph.add_node("employment_income",     self._node_employment_income)
        graph.add_node("loan_purpose",          self._node_loan_purpose)
        graph.add_node("risk_assessment",       self._node_risk_assessment)
        graph.add_node("offer_acceptance",      self._node_offer_acceptance)
        graph.add_node("human_escalation",      self._node_human_escalation)
        graph.add_node("completed",             self._node_completed)

        # ── Entry ─────────────────────────────────────────────────────────────
        graph.set_entry_point("greeting_consent")

        # ── Conditional edges (V-CIP flow) ────────────────────────────────────
        for node, next_node in [
            ("greeting_consent",     "ovd_document_capture"),
            ("ovd_document_capture", "liveness_challenge"),
            ("liveness_challenge",   "aadhaar_verification"),
            ("aadhaar_verification", "identity_kyc"),
            ("identity_kyc",         "employment_income"),
            ("employment_income",    "loan_purpose"),
            ("loan_purpose",         "risk_assessment"),
            ("risk_assessment",      "offer_acceptance"),
            ("offer_acceptance",     "completed"),
        ]:
            graph.add_conditional_edges(
                node,
                self._make_router(node, next_node),
                {
                    "advance":  next_node,
                    "retry":    node,               # loop back on retry
                    "escalate": "human_escalation",
                    "end":      END,
                },
            )

        graph.add_edge("human_escalation", END)
        graph.add_edge("completed", END)

        return graph.compile(
            checkpointer=self._checkpointer,
        )

    # ── Router factory ────────────────────────────────────────────────────────

    def _make_router(self, current: str, _next: str):
        """Returns a routing function for the given node."""

        async def router(state: GlobalGraphState) -> StageDecision:
            call_id = state.get("call_id")
            raw = await redis_client.get_state(f"session:{call_id}:state")
            if not raw:
                return "end"
            shared: SharedState = SharedState.from_json(raw)

            stage_result = state.get("current_stage_result", {})
            passed      = stage_result.get("passed", False)
            escalate    = stage_result.get("escalate", False)

            if escalate:
                await self._log_moderator(shared, current, "ESCALATE", stage_result)
                return "escalate"

            if passed:
                await self._log_moderator(shared, current, "ADVANCE", stage_result)
                await self._apply_state_delta(
                    call_id,
                    shared.version,
                    {"stage_retry_count": 0},
                )
                return "advance"

            # Retry logic
            shared.stage_retry_count += 1
            if shared.stage_retry_count >= shared.max_retries_per_stage:
                await self._log_moderator(shared, current, "ESCALATE_MAX_RETRIES", stage_result)
                return "escalate"

            await self._log_moderator(shared, current, "RETRY", stage_result)
            await self._apply_state_delta(
                call_id,
                shared.version,
                {"stage_retry_count": shared.stage_retry_count},
            )
            return "retry"

        return router

    # ── Stage nodes ───────────────────────────────────────────────────────────

    async def _update_stage(self, call_id: str, new_stage: SessionStage):
        """Update the SharedState current_stage and notify the frontend."""
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if raw:
            shared = SharedState.from_json(raw)
            await self._apply_state_delta(
                call_id,
                shared.version,
                {"current_stage": new_stage.value},
            )
            
            # Notify frontend
            await redis_client.publish(f"session:{call_id}:events", {
                "event": "STAGE_CHANGED",
                "stage": new_stage.value,
                "call_id": call_id,
            })

    async def _node_greeting_consent(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        await self._update_stage(call_id, SessionStage.GREETING_CONSENT)
        await rabbitmq_client.publish_task("compliance",   {"call_id": call_id, "action": "check_consent_requirement"})
        await rabbitmq_client.publish_task("conversation", {"call_id": call_id, "action": "greet_and_request_consent"})
        
        stage_result = interrupt("waiting_for_agent")
        return {"current_stage_result": stage_result, "stage_history": [stage_result]}

    async def _node_ovd_document_capture(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        await self._update_stage(call_id, SessionStage.OVD_DOCUMENT_CAPTURE)
        await rabbitmq_client.publish_task("conversation", {"call_id": call_id, "action": "request_ovd_document"})
        
        stage_result = interrupt("waiting_for_agent")
        return {"current_stage_result": stage_result, "stage_history": [stage_result]}

    async def _node_liveness_challenge(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        await self._update_stage(call_id, SessionStage.LIVENESS_CHALLENGE)
        await rabbitmq_client.publish_task("conversation", {"call_id": call_id, "action": "run_liveness_challenge"})
        await rabbitmq_client.publish_task("vision",       {"call_id": call_id, "action": "run_liveness_age_check"})
        
        stage_result = interrupt("waiting_for_agent")
        return {"current_stage_result": stage_result, "stage_history": [stage_result]}

    async def _node_aadhaar_verification(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        await self._update_stage(call_id, SessionStage.AADHAAR_VERIFICATION)
        await rabbitmq_client.publish_task("conversation", {"call_id": call_id, "action": "request_aadhaar_otp"})
        
        stage_result = interrupt("waiting_for_agent")
        return {"current_stage_result": stage_result, "stage_history": [stage_result]}

    async def _node_identity_kyc(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        await self._update_stage(call_id, SessionStage.IDENTITY_KYC)
        await rabbitmq_client.publish_task("verification", {"call_id": call_id, "action": "verify_identity_documents"})
        await rabbitmq_client.publish_task("conversation", {"call_id": call_id, "action": "collect_identity_info"})
        
        stage_result = interrupt("waiting_for_agent")
        return {"current_stage_result": stage_result, "stage_history": [stage_result]}

    async def _node_employment_income(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        await self._update_stage(call_id, SessionStage.EMPLOYMENT_INCOME)
        await rabbitmq_client.publish_task("conversation", {"call_id": call_id, "action": "collect_employment_income"})
        await rabbitmq_client.publish_task("verification", {"call_id": call_id, "action": "validate_income_declaration"})
        
        stage_result = interrupt("waiting_for_agent")
        return {"current_stage_result": stage_result, "stage_history": [stage_result]}

    async def _node_loan_purpose(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        await self._update_stage(call_id, SessionStage.LOAN_PURPOSE)
        await rabbitmq_client.publish_task("conversation", {"call_id": call_id, "action": "collect_loan_purpose"})
        
        stage_result = interrupt("waiting_for_agent")
        return {"current_stage_result": stage_result, "stage_history": [stage_result]}

    async def _node_risk_assessment(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        await self._update_stage(call_id, SessionStage.RISK_ASSESSMENT)
        await rabbitmq_client.publish_task("risk", {"call_id": call_id, "action": "full_risk_assessment"})
        
        stage_result = interrupt("waiting_for_agent")
        return {"current_stage_result": stage_result, "stage_history": [stage_result]}

    async def _node_offer_acceptance(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        # Zero-trust gate: final offer stage only after verified income
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if raw:
            shared = SharedState.from_json(raw)
            verified_income = shared.financial_data.verified_income or 0
            if verified_income <= 0:
                await self._log_moderator(shared, "offer_acceptance", "PENDING_DOCS", {
                    "agent": "moderator",
                    "confidence": 1.0,
                })
                await redis_client.publish(f"session:{call_id}:events", {
                    "event": "ELIGIBILITY_PENDING_DOCS",
                    "call_id": call_id,
                    "reason": "verified_income_missing",
                })
                return {"current_stage_result": {"passed": True}, "stage_history": [{"passed": True}]}

        await self._update_stage(call_id, SessionStage.OFFER_ACCEPTANCE)
        await rabbitmq_client.publish_task("offer",      {"call_id": call_id, "action": "generate_offer"})
        await rabbitmq_client.publish_task("compliance", {"call_id": call_id, "action": "validate_offer_compliance"})
        
        stage_result = interrupt("waiting_for_agent")
        return {"current_stage_result": stage_result, "stage_history": [stage_result]}

    async def _node_human_escalation(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        await rabbitmq_client.publish_task("human_oversight", {
            "call_id": call_id,
            "reason":  state.get("current_stage_result", {}).get("escalation_reason", "threshold_exceeded"),
        })
        # Notify frontend via Redis pub/sub
        await redis_client.publish(f"session:{call_id}:events", {
            "event": "HUMAN_ESCALATION",
            "call_id": call_id,
        })
        return state

    async def _node_completed(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if raw:
            shared = SharedState.from_json(raw)
            await self._apply_state_delta(
                call_id,
                shared.version,
                {"current_stage": SessionStage.COMPLETED.value},
            )

        await redis_client.set_once(f"session:{call_id}:stopped")
        await redis_client.publish(f"session:{call_id}:events", {
            "event": "SESSION_COMPLETED",
            "call_id": call_id,
        })
        logger.info(f"✅ Session completed: {call_id}")
        return state

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _log_moderator(self, shared: SharedState, stage: str, action: str, result: dict):
        entry = ModeratorLogEntry(
            stage=stage,
            agent_activated=result.get("agent", "moderator"),
            action_taken=action,
            confidence=result.get("confidence", 0.0),
            escalated_to_human=(action in ("ESCALATE", "ESCALATE_MAX_RETRIES")),
            timestamp=time.time(),
        )
        shared.moderator_log.append(entry)
        await self._apply_state_delta(
            shared.session_meta.call_id,
            shared.version,
            {"moderator_log": [m.__dict__ for m in shared.moderator_log]},
        )

    async def _apply_state_delta(self, call_id: str, expected_version: int, delta: dict) -> bool:
        """
        Single supervisor-owned atomic patch application with bounded CAS retries.
        """
        state_key = f"session:{call_id}:state"
        max_attempts = 3
        current_expected = expected_version
        for _ in range(max_attempts):
            ok, resulting_version = await redis_client.compare_and_set_state(
                state_key,
                current_expected,
                delta,
            )
            if ok:
                return True
            self._conflict_count += 1
            self._retry_count += 1
            if resulting_version < 0:
                return False
            current_expected = resulting_version
        logger.warning("State CAS apply failed after retries", extra={
            "call_id": call_id,
            "delta_keys": list(delta.keys()),
            "conflict_count": self._conflict_count,
            "retry_count": self._retry_count,
        })
        return False

    # ── Public API ────────────────────────────────────────────────────────────

    async def advance_stage(self, call_id: str, stage_result: dict) -> dict:
        """Called by each worker agent upon task completion."""
        config = {"configurable": {"thread_id": call_id}}
        result = await self._graph.ainvoke(
            Command(resume=stage_result),
            config=config,
        )
        return result

    async def handle_stt_processed(
        self,
        call_id: str,
        confidence: float,
        consent_present: bool,
        stage: str,
    ) -> None:
        """
        Strict supervisor routing for STT outcomes.
        Now checks if required entities for the current stage are actually
        present before advancing — prevents premature stage advancement.
        """
        if confidence < 0.75:
            await rabbitmq_client.publish_task("conversation", {
                "call_id": call_id,
                "action": "re_ask_last_question",
                "reason": "low_stt_confidence",
                "confidence": confidence,
            })
            return

        # Stage-specific entity gate: check if we have the data needed to advance
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            return
        shared = SharedState.from_json(raw)

        # Drop stale STT events that belong to an older stage.
        current_stage = shared.current_stage.value
        if stage != current_stage:
            logger.info(
                "Dropping stale STT event for %s: event_stage=%s current_stage=%s",
                call_id,
                stage,
                current_stage,
            )
            return

        entity_check = STAGE_REQUIRED_ENTITIES.get(stage)
        if entity_check and not entity_check(shared):
            # Required entities are missing — re-ask
            reason = STAGE_REASK_REASON.get(stage, "low_stt_confidence")
            logger.info(
                f"Stage {stage} entity gate FAILED for {call_id} — "
                f"re-asking with reason={reason}"
            )
            await rabbitmq_client.publish_task("conversation", {
                "call_id": call_id,
                "action": "re_ask_last_question",
                "reason": reason,
                "confidence": confidence,
            })
            return

        # Stage-level idempotency guard to prevent duplicate "confirm_and_advance"
        # when repeated transcripts arrive before stage transition fully settles.
        lock_key = f"session:{call_id}:advance-lock:{stage}"
        acquired = await redis_client.set_once(lock_key, "1", ttl_seconds=8)
        if not acquired:
            logger.info(
                "Skipping duplicate stage-advance trigger for %s at stage=%s",
                call_id,
                stage,
            )
            return

        # All entity checks passed — confirm and advance
        await rabbitmq_client.publish_task("conversation", {
            "call_id": call_id,
            "action": "confirm_and_advance",
            "confidence": confidence,
        })

    async def handle_snapshot_received(self, call_id: str) -> None:
        """
        Supervisor-controlled vision dispatch from snapshot ingress.
        """
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            return
        shared = SharedState.from_json(raw)
        # Run vision during KYC, OVD, and Liveness stages
        if shared.current_stage in (
            SessionStage.IDENTITY_KYC,
            SessionStage.OVD_DOCUMENT_CAPTURE,
            SessionStage.LIVENESS_CHALLENGE,
        ):
            await rabbitmq_client.publish_task("vision", {
                "call_id": call_id,
                "action": "run_liveness_age_check",
                "source": "canvas_snapshot",
            })

    async def start_session(self, call_id: str) -> None:
        """Kick off the DAG from Stage 1."""
        config = {"configurable": {"thread_id": call_id}}
        await self._graph.ainvoke({"call_id": call_id}, config=config)


moderator_engine = ModeratorEngine()
