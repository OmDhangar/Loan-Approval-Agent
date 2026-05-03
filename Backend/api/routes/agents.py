"""
Agents API Routes
──────────────────
POST /api/v1/agents/{call_id}/offer/accept   – Customer accepts offer (UPI trigger)
POST /api/v1/agents/{call_id}/offer/decline  – Customer declines offer
GET  /api/v1/agents/{call_id}/stage          – Current stage + last agent message
POST /api/v1/agents/{call_id}/escalate       – Manual human escalation trigger
"""

import time
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.redis_client import redis_client
from core.rabbitmq_client import rabbitmq_client
from core.langgraph_engine import moderator_engine
from models.shared_state import SharedState, SessionStage

logger = logging.getLogger(__name__)
router = APIRouter()


class OfferAcceptRequest(BaseModel):
    tenure: int   # Selected tenure in months


class EscalateRequest(BaseModel):
    reason: str = "customer_requested"


@router.post("/{call_id}/offer/accept")
async def accept_offer(call_id: str, req: OfferAcceptRequest):
    """
    Customer taps 'Accept via UPI'.
    Records acceptance in state and triggers loan processing pipeline.
    """
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    state = SharedState.from_json(raw)
    offer = state.final_offer

    if offer.eligible_amount is None:
        raise HTTPException(status_code=400, detail="No active offer for this session")
    if offer.acceptance_status == "ELIGIBILITY_PENDING_DOCS":
        raise HTTPException(status_code=409, detail="Eligibility pending documents. Offer cannot be accepted yet.")

    offer.acceptance_status  = "ACCEPTED"
    offer.accepted_tenure    = req.tenure
    state.final_offer        = offer
    state.current_stage      = SessionStage.COMPLETED
    state.version           += 1
    await redis_client.set_state(state.redis_key(), state.to_json())

    # Notify frontend
    await redis_client.publish(f"session:{call_id}:events", {
        "event":   "OFFER_ACCEPTED",
        "tenure":  req.tenure,
        "amount":  offer.eligible_amount,
        "call_id": call_id,
    })

    # Advance Moderator to completion
    await moderator_engine.advance_stage(call_id, {
        "passed":     True,
        "agent":      "offer_acceptance",
        "confidence": 1.0,
        "accepted":   True,
    })

    logger.info(f"Offer accepted: {call_id} | tenure={req.tenure}m | amount={offer.eligible_amount}")
    return {
        "status":   "accepted",
        "call_id":  call_id,
        "amount":   offer.eligible_amount,
        "tenure":   req.tenure,
        "next_step": "whatsapp_followup",
    }


@router.post("/{call_id}/offer/decline")
async def decline_offer(call_id: str):
    """Customer declines the offer."""
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    state = SharedState.from_json(raw)
    state.final_offer.acceptance_status = "DECLINED"
    state.current_stage = SessionStage.COMPLETED
    state.version += 1
    await redis_client.set_state(state.redis_key(), state.to_json())

    await redis_client.publish(f"session:{call_id}:events", {
        "event":   "OFFER_DECLINED",
        "call_id": call_id,
    })

    logger.info(f"Offer declined: {call_id}")
    return {"status": "declined", "call_id": call_id}


@router.get("/{call_id}/stage")
async def get_stage(call_id: str):
    """Return the current stage and progress details."""
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    state = SharedState.from_json(raw)
    return {
        "call_id":      call_id,
        "stage":        state.current_stage.value,
        "retry_count":  state.stage_retry_count,
        "version":      state.version,
        "quality_score": state.session_meta.network_quality_score,
        "consent_given": state.customer_identity.consent_given,
        "liveness_ok":  state.customer_identity.liveness_passed,
        "risk_band":    state.financial_data.risk_band.value,
    }


@router.post("/{call_id}/escalate")
async def manual_escalate(call_id: str, req: EscalateRequest):
    """Manually trigger human escalation (e.g. customer presses 'Talk to Human')."""
    raw = await redis_client.get_state(f"session:{call_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    state = SharedState.from_json(raw)
    state.current_stage = SessionStage.ESCALATED
    state.version += 1
    await redis_client.set_state(state.redis_key(), state.to_json())

    await rabbitmq_client.publish_task("human_oversight", {
        "call_id": call_id,
        "reason":  req.reason,
    })

    await redis_client.publish(f"session:{call_id}:events", {
        "event":   "HUMAN_ESCALATION",
        "reason":  req.reason,
        "call_id": call_id,
    })

    logger.info(f"Manual escalation triggered: {call_id} | reason={req.reason}")
    return {"status": "escalated", "call_id": call_id}