"""
Offer Agent
────────────
Activated in Offer Acceptance stage by SessionOrchestrator (background task).

Refactor changes:
  - Removed LangGraph dependency (uses SessionOrchestrator directly)
  - Filler speech now fires via EventBus-aware Redis publish
  - No functional changes to policy engine or EMI calculation
"""

import logging
import time
from typing import Optional

from models.shared_state import SharedState, LoanOffer, RiskBand
from core.redis_client import redis_client
from core.langgraph_engine import moderator_engine
from services.llm_gateway import llm_gateway
from core.config import settings

logger = logging.getLogger(__name__)


POLICY = {
    "min_cibil":          650,
    "min_income":         15_000,
    "max_foir_post_loan": 0.55,
    "max_loan_amount":    1_000_000,
    "min_loan_amount":    20_000,
    "repo_rate":          6.50,
    "income_multiplier": {
        RiskBand.LOW:    40,
        RiskBand.MEDIUM: 25,
        RiskBand.HIGH:   0,
    },
    "credit_spread": {
        (780, 900): 3.5,
        (750, 779): 4.0,
        (720, 749): 5.0,
        (700, 719): 5.5,
        (650, 699): 6.5,
    },
    "tenures": {
        RiskBand.LOW:    [12, 24, 36, 48, 60],
        RiskBand.MEDIUM: [12, 24, 36],
        RiskBand.HIGH:   [],
    },
}


class OfferAgent:

    async def handle_task(self, payload: dict):
        call_id = payload["call_id"]
        action  = payload.get("action", "generate_offer")

        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            return
        state = SharedState.from_json(raw)

        if action == "generate_offer":
            await self._generate_offer(call_id, state)

    async def _generate_offer(self, call_id: str, state: SharedState):
        """Two-phase: deterministic policy engine → LLM explanation."""
        eligibility = self._run_policy_engine(state)

        if not eligibility["eligible"]:
            pending_docs = eligibility.get("status") == "ELIGIBILITY_PENDING_DOCS"
            state.final_offer.acceptance_status = (
                "ELIGIBILITY_PENDING_DOCS" if pending_docs else "DECLINED_INELIGIBLE"
            )
            if pending_docs:
                state.financial_data.income_verification_source = "pending_docs"
            state.version += 1
            await redis_client.set_state(state.redis_key(), state.to_json())

            await redis_client.publish(f"session:{call_id}:events", {
                "event":   "ELIGIBILITY_PENDING_DOCS" if pending_docs else "OFFER_DECLINED_INELIGIBLE",
                "reason":  eligibility["reason"],
                "call_id": call_id,
                "ts":      time.time(),
            })
            await moderator_engine.advance_stage(call_id, {
                "passed": True, "agent": "offer", "confidence": 1.0, "ineligible": True,
            })
            return

        # Send filler speech while LLM generates explanation (non-blocking)
        name = state.customer_identity.name or "there"
        await redis_client.publish(f"session:{call_id}:events", {
            "event":     "AI_AGENT_SPEECH",
            "text":      f"One moment {name}, I'm finalising your personalised offer right now…",
            "is_filler": True,
            "call_id":   call_id,
            "ts":        time.time(),
        })

        explanation = await self._generate_explanation(call_id, state, eligibility)

        offer = LoanOffer(
            eligible_amount=eligibility["eligible_amount"],
            tenure_options=eligibility["tenure_options"],
            interest_rate=eligibility["interest_rate"],
            emi_12m=self._calc_emi(eligibility["eligible_amount"], eligibility["interest_rate"], 12),
            emi_24m=self._calc_emi(eligibility["eligible_amount"], eligibility["interest_rate"], 24),
            emi_36m=self._calc_emi(eligibility["eligible_amount"], eligibility["interest_rate"], 36),
            kfs_url=f"https://loanwizard.poonawallafincorp.com/kfs/{call_id}",
            offer_explanation=explanation,
            acceptance_status="PENDING",
        )
        state.final_offer = offer
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        await redis_client.publish(f"session:{call_id}:events", {
            "event":   "OFFER_READY",
            "call_id": call_id,
            "ts":      time.time(),
            "offer": {
                "eligible_amount":  offer.eligible_amount,
                "tenure_options":   offer.tenure_options,
                "interest_rate":    offer.interest_rate,
                "emi_12m":          offer.emi_12m,
                "emi_24m":          offer.emi_24m,
                "emi_36m":          offer.emi_36m,
                "kfs_url":          offer.kfs_url,
                "explanation":      offer.offer_explanation,
                "rate_adjustments": eligibility.get("rate_adjustments", []),
            },
        })

        await moderator_engine.advance_stage(call_id, {
            "passed": True, "agent": "offer", "confidence": 1.0,
        })

    def _run_policy_engine(self, state: SharedState) -> dict:
        fd        = state.financial_data
        income    = fd.verified_income or 0
        cibil     = fd.bureau_score or 0
        risk_band = fd.risk_band
        foir      = fd.foir or 0.0
        existing_emi = fd.existing_emi_total or 0

        if income <= 0:
            return {
                "eligible": False,
                "status": "ELIGIBILITY_PENDING_DOCS",
                "reason": "Verified income missing. Please upload income proof to continue.",
            }
        if risk_band == RiskBand.HIGH:
            return {"eligible": False, "reason": "Risk band HIGH — refer to human review"}
        if income < POLICY["min_income"]:
            return {"eligible": False, "reason": f"Income ₹{income:,.0f} below minimum ₹{POLICY['min_income']:,}"}
        if cibil > 0 and cibil < POLICY["min_cibil"]:
            return {"eligible": False, "reason": f"CIBIL {cibil} below minimum {POLICY['min_cibil']}"}

        multiplier          = POLICY["income_multiplier"].get(risk_band, 25)
        max_by_income       = min(income * multiplier, POLICY["max_loan_amount"])
        max_after_oblig     = max(max_by_income - existing_emi * 12, 0)
        requested           = state.extracted_signals.loan_amount_requested or max_after_oblig
        eligible            = min(max(min(requested, max_after_oblig), POLICY["min_loan_amount"]), POLICY["max_loan_amount"])

        rate_result         = self._calculate_interest_rate(cibil, fd)
        proposed_emi        = self._calc_emi(eligible, rate_result["final_rate"], 24)
        post_loan_foir      = (existing_emi + proposed_emi) / income if income > 0 else 1.0

        if post_loan_foir > POLICY["max_foir_post_loan"]:
            max_new_emi = income * POLICY["max_foir_post_loan"] - existing_emi
            if max_new_emi <= 0:
                return {"eligible": False, "reason": f"FOIR too high ({post_loan_foir:.0%}) — existing EMIs exhaust capacity"}
            eligible = round(max(self._calc_principal_from_emi(max_new_emi, rate_result["final_rate"], 24), POLICY["min_loan_amount"]), -3)
            if eligible < POLICY["min_loan_amount"]:
                return {"eligible": False, "reason": f"Affordable amount below minimum ₹{POLICY['min_loan_amount']:,}"}

        return {
            "eligible":         True,
            "eligible_amount":  round(eligible, -3),
            "interest_rate":    rate_result["final_rate"],
            "tenure_options":   POLICY["tenures"].get(risk_band, [12, 24, 36]),
            "rate_adjustments": rate_result["adjustments"],
            "post_loan_foir":   round(post_loan_foir, 2),
            "reason":           "eligible",
        }

    def _calculate_interest_rate(self, cibil: int, fd: object) -> dict:
        spread = 7.0
        for (low, high), s in POLICY["credit_spread"].items():
            if low <= cibil <= high:
                spread = s
                break

        base_rate   = POLICY["repo_rate"] + spread
        adjustments = []

        foir = getattr(fd, "foir", 0) or 0
        if foir > 0.50:
            base_rate += 0.5; adjustments.append("+0.50% high FOIR")

        emp_years = getattr(fd, "employment_stability_years", 0) or 0
        if emp_years < 2.0:
            base_rate += 0.5; adjustments.append("+0.50% short employment")

        util = getattr(fd, "credit_utilization", 0) or 0
        if util > 0.60:
            base_rate += 0.5; adjustments.append("+0.50% high utilisation")

        if getattr(fd, "verified_income", None):
            base_rate -= 0.25; adjustments.append("-0.25% bank-verified income")

        delinq = getattr(fd, "delinquency_count", 0) or 0
        if delinq == 0 and emp_years >= 5.0:
            base_rate -= 0.25; adjustments.append("-0.25% clean history + stable employment")

        return {
            "final_rate":   round(max(9.0, min(18.0, base_rate)), 2),
            "base_rate":    POLICY["repo_rate"] + spread,
            "adjustments":  adjustments,
        }

    async def _generate_explanation(self, call_id: str, state: SharedState, eligibility: dict) -> str:
        name    = state.customer_identity.name or "Customer"
        income  = state.financial_data.verified_income or state.financial_data.monthly_income or 0
        cibil   = state.financial_data.bureau_score or 0
        amt     = eligibility["eligible_amount"]
        rate    = eligibility["interest_rate"]
        emi24   = self._calc_emi(amt, rate, 24)
        purpose = state.extracted_signals.loan_purpose or "your requirement"

        prompt = (
            f"You are a friendly Indian bank loan officer on a video call.\n"
            f"Generate a warm, clear 2-sentence explanation of their loan offer. No jargon.\n\n"
            f"Customer: {name}\nMonthly income: ₹{income:,.0f}\nCIBIL: {cibil}\n"
            f"Purpose: {purpose}\nApproved: ₹{amt:,.0f} at {rate}% p.a.\n"
            f"EMI (24m): ₹{emi24:,.0f}/month\n\n"
            f"Write ONLY the 2-sentence explanation. Be warm and encouraging. Mention their name."
        )

        text = await llm_gateway.generate_text(
            model=settings.LLM_MODEL_LARGE,
            prompt=prompt,
            num_predict=60,
            timeout=12,
        )
        if text:
            return text

        return (
            f"{name}, based on your ₹{income:,.0f} monthly income and CIBIL score of {cibil}, "
            f"you qualify for a loan of ₹{amt:,.0f} at {rate}% per annum. "
            f"Your EMI for 24 months would be just ₹{emi24:,.0f}/month!"
        )

    @staticmethod
    def _calc_emi(principal: float, annual_rate: float, months: int) -> float:
        if principal <= 0 or months <= 0:
            return 0.0
        r = (annual_rate / 100) / 12
        if r == 0:
            return round(principal / months, 2)
        return round(principal * r * (1 + r) ** months / ((1 + r) ** months - 1), 2)

    @staticmethod
    def _calc_principal_from_emi(emi: float, annual_rate: float, months: int) -> float:
        if emi <= 0 or months <= 0:
            return 0.0
        r = (annual_rate / 100) / 12
        if r == 0:
            return emi * months
        return round(emi * ((1 + r) ** months - 1) / (r * (1 + r) ** months), 2)