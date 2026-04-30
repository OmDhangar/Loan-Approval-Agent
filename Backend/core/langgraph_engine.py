"""
LangGraph Moderator Engine
──────────────────────────
Implements the Supervisor-Worker Multi-Agent DAG.
Each node = one stage of the loan journey.
Conditional edges handle: advance / retry / escalate.

Stage flow:
  INIT → GREETING_CONSENT → IDENTITY_KYC → EMPLOYMENT_INCOME
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


class ModeratorEngine:
    """
    Central LangGraph DAG controller.
    Called by the Moderator on every stage completion event.
    """

    def __init__(self):
        self._checkpointer = MemorySaver()
        self._graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(GlobalGraphState)

        # ── Nodes (one per stage) ─────────────────────────────────────────────
        graph.add_node("greeting_consent",   self._node_greeting_consent)
        graph.add_node("identity_kyc",       self._node_identity_kyc)
        graph.add_node("employment_income",  self._node_employment_income)
        graph.add_node("loan_purpose",       self._node_loan_purpose)
        graph.add_node("risk_assessment",    self._node_risk_assessment)
        graph.add_node("offer_acceptance",   self._node_offer_acceptance)
        graph.add_node("human_escalation",   self._node_human_escalation)
        graph.add_node("completed",          self._node_completed)

        # ── Entry ─────────────────────────────────────────────────────────────
        graph.set_entry_point("greeting_consent")

        # ── Conditional edges ─────────────────────────────────────────────────
        for node, next_node in [
            ("greeting_consent",  "identity_kyc"),
            ("identity_kyc",      "employment_income"),
            ("employment_income", "loan_purpose"),
            ("loan_purpose",      "risk_assessment"),
            ("risk_assessment",   "offer_acceptance"),
            ("offer_acceptance",  "completed"),
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
                shared.stage_retry_count = 0
                return "advance"

            # Retry logic
            shared.stage_retry_count += 1
            if shared.stage_retry_count >= shared.max_retries_per_stage:
                await self._log_moderator(shared, current, "ESCALATE_MAX_RETRIES", stage_result)
                return "escalate"

            await self._log_moderator(shared, current, "RETRY", stage_result)
            await redis_client.set_state(shared.redis_key(), shared.to_json())
            return "retry"

        return router

    # ── Stage nodes ───────────────────────────────────────────────────────────

    async def _update_stage(self, call_id: str, new_stage: SessionStage):
        """Update the SharedState current_stage and notify the frontend."""
        raw = await redis_client.get_state(f"session:{call_id}:state")
        if raw:
            shared = SharedState.from_json(raw)
            shared.current_stage = new_stage
            shared.version += 1
            await redis_client.set_state(shared.redis_key(), shared.to_json())
            
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

    async def _node_identity_kyc(self, state: GlobalGraphState) -> GlobalGraphState:
        call_id = state["call_id"]
        await self._update_stage(call_id, SessionStage.IDENTITY_KYC)
        await rabbitmq_client.publish_task("vision",       {"call_id": call_id, "action": "run_liveness_age_check"})
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
            shared.current_stage = SessionStage.COMPLETED
            await redis_client.set_state(shared.redis_key(), shared.to_json())

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
        await redis_client.set_state(shared.redis_key(), shared.to_json())

    # ── Public API ────────────────────────────────────────────────────────────

    async def advance_stage(self, call_id: str, stage_result: dict) -> dict:
        """Called by each worker agent upon task completion."""
        config = {"configurable": {"thread_id": call_id}}
        result = await self._graph.ainvoke(
            Command(resume=stage_result),
            config=config,
        )
        return result

    async def start_session(self, call_id: str) -> None:
        """Kick off the DAG from Stage 1."""
        config = {"configurable": {"thread_id": call_id}}
        await self._graph.ainvoke({"call_id": call_id}, config=config)


moderator_engine = ModeratorEngine()
