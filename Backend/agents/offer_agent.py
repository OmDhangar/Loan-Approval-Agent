"""
Offer Agent
────────────
Activated in Stage 6 (Offer & Acceptance).
Steps:
  1. Policy engine: deterministic eligibility rules using bureau-enriched data
  2. FOIR-aware loan amount calculation with dynamic rate adjustments
  3. LLM layer: Gemma 3 27B generates plain-language explanation
  4. Result pushed to frontend via Redis pub/sub
     (VideoSDK DataChannel equivalent — rendered as overlay in call)
"""

import logging
import json
import time
from typing import Optional

import httpx

from models.shared_state import SharedState, LoanOffer, RiskBand
from core.redis_client import redis_client
from core.rabbitmq_client import rabbitmq_client
from core.config import settings
from core.langgraph_engine import moderator_engine

logger = logging.getLogger(__name__)


# ── Lending policy constants (externalise to YAML in production) ──────────────

POLICY = {
    "min_cibil":                650,
    "min_income":               15_000,      # ₹15,000/month
    "max_foir_post_loan":       0.55,        # Max FOIR after adding new EMI
    "max_loan_amount":          1_000_000,   # ₹10 lakhs
    "min_loan_amount":          20_000,      # ₹20,000
    "repo_rate":                6.50,        # RBI repo rate (base)

    # Income multiplier by risk band
    "income_multiplier": {
        RiskBand.LOW:    40,     # Max loan = 40× monthly income
        RiskBand.MEDIUM: 25,     # Max loan = 25× monthly income
        RiskBand.HIGH:   0,      # Rejected
    },

    # Credit spread over repo rate by CIBIL band
    "credit_spread": {
        (780, 900): 3.5,    # 10.0% total
        (750, 779): 4.0,    # 10.5% total
        (720, 749): 5.0,    # 11.5% total
        (700, 719): 5.5,    # 12.0% total
        (650, 699): 6.5,    # 13.0% total
    },

    # Eligible tenures by risk band
    "tenures": {
        RiskBand.LOW:    [12, 24, 36, 48, 60],
        RiskBand.MEDIUM: [12, 24, 36],
        RiskBand.HIGH:   [],     # Rejected
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
        """
        Two-phase offer generation:
        Phase 1 – Deterministic policy engine (bureau-enriched)
        Phase 2 – LLM explanation layer
        """

        # ── Phase 1: Policy engine ────────────────────────────────────────────
        eligibility = self._run_policy_engine(state)

        if not eligibility["eligible"]:
            logger.info(f"Customer ineligible: {call_id} | reason: {eligibility['reason']}")
            state.final_offer.acceptance_status = "DECLINED_INELIGIBLE"
            state.version += 1
            await redis_client.set_state(state.redis_key(), state.to_json())

            await redis_client.publish(f"session:{call_id}:events", {
                "event":   "OFFER_DECLINED_INELIGIBLE",
                "reason":  eligibility["reason"],
                "call_id": call_id,
            })

            await moderator_engine.advance_stage(call_id, {
                "passed":    True,
                "agent":     "offer",
                "confidence": 1.0,
                "ineligible": True,
            })
            return

        # ── Phase 2: LLM explanation ──────────────────────────────────────────
        explanation = await self._generate_explanation(state, eligibility)

        # Build offer object
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

        # ── Push offer to frontend (VideoSDK DataChannel equivalent) ─────────
        await redis_client.publish(f"session:{call_id}:events", {
            "event":   "OFFER_READY",
            "call_id": call_id,
            "offer": {
                "eligible_amount": offer.eligible_amount,
                "tenure_options":  offer.tenure_options,
                "interest_rate":   offer.interest_rate,
                "emi_12m":         offer.emi_12m,
                "emi_24m":         offer.emi_24m,
                "emi_36m":         offer.emi_36m,
                "kfs_url":         offer.kfs_url,
                "explanation":     offer.offer_explanation,
                "rate_adjustments": eligibility.get("rate_adjustments", []),
            },
        })

        await moderator_engine.advance_stage(call_id, {
            "passed":     True,
            "agent":      "offer",
            "confidence": 1.0,
        })

    # ── Policy Engine ─────────────────────────────────────────────────────────

    def _run_policy_engine(self, state: SharedState) -> dict:
        """
        Deterministic lending policy check using bureau-enriched data.
        LLM is NOT used here – numbers come from state directly.
        """
        fd        = state.financial_data
        income    = fd.verified_income or fd.monthly_income or 0
        cibil     = fd.bureau_score or 0
        risk_band = fd.risk_band
        foir      = fd.foir or 0.0
        existing_emi = fd.existing_emi_total or 0

        # ── Rejection checks ─────────────────────────────────────────────

        if risk_band == RiskBand.HIGH:
            return {"eligible": False, "reason": "Risk band HIGH – refer to human review"}

        if income < POLICY["min_income"]:
            return {"eligible": False, "reason": f"Income ₹{income:,.0f} below minimum ₹{POLICY['min_income']:,}"}

        if cibil > 0 and cibil < POLICY["min_cibil"]:
            return {"eligible": False, "reason": f"CIBIL {cibil} below minimum {POLICY['min_cibil']}"}

        # ── Calculate eligible amount ─────────────────────────────────────

        multiplier = POLICY["income_multiplier"].get(risk_band, 25)
        max_by_income = min(income * multiplier, POLICY["max_loan_amount"])

        # Deduct existing obligations
        max_after_obligations = max_by_income - (existing_emi * 12)
        max_after_obligations = max(max_after_obligations, 0)

        requested = state.extracted_signals.loan_amount_requested or max_after_obligations
        eligible  = min(requested, max_after_obligations)
        eligible  = max(eligible, POLICY["min_loan_amount"])
        eligible  = min(eligible, POLICY["max_loan_amount"])

        # ── FOIR check with proposed loan ─────────────────────────────────
        # Estimate proposed EMI at 24-month tenure for FOIR check
        estimated_rate = self._calculate_interest_rate(cibil, fd)
        proposed_emi = self._calc_emi(eligible, estimated_rate["final_rate"], 24)
        post_loan_foir = (existing_emi + proposed_emi) / income if income > 0 else 1.0

        if post_loan_foir > POLICY["max_foir_post_loan"]:
            # Reduce loan amount to fit within FOIR limit
            max_new_emi = (income * POLICY["max_foir_post_loan"]) - existing_emi
            if max_new_emi <= 0:
                return {"eligible": False, "reason": f"FOIR too high ({post_loan_foir:.0%}) — existing EMIs exhaust capacity"}

            # Back-calculate max principal from affordable EMI
            eligible = self._calc_principal_from_emi(max_new_emi, estimated_rate["final_rate"], 24)
            eligible = round(max(eligible, POLICY["min_loan_amount"]), -3)
            if eligible < POLICY["min_loan_amount"]:
                return {"eligible": False, "reason": f"Affordable amount below minimum ₹{POLICY['min_loan_amount']:,}"}

        # ── Interest rate ─────────────────────────────────────────────────
        rate_result = self._calculate_interest_rate(cibil, fd)

        # ── Tenure options ────────────────────────────────────────────────
        tenures = POLICY["tenures"].get(risk_band, [12, 24, 36])

        return {
            "eligible":        True,
            "eligible_amount": round(eligible, -3),   # Round to nearest ₹1000
            "interest_rate":   rate_result["final_rate"],
            "tenure_options":  tenures,
            "rate_adjustments": rate_result["adjustments"],
            "post_loan_foir":  round(post_loan_foir, 2),
            "reason":          "eligible",
        }

    def _calculate_interest_rate(self, cibil: int, fd) -> dict:
        """
        Calculate interest rate with dynamic adjustments.
        Base = repo_rate + credit_spread[cibil_band]
        Then apply adjustments for FOIR, stability, utilisation, etc.
        """
        # Base spread from CIBIL band
        spread = 7.0  # Default spread (highest)
        for (low, high), s in POLICY["credit_spread"].items():
            if low <= cibil <= high:
                spread = s
                break

        base_rate = POLICY["repo_rate"] + spread
        adjustments = []

        # ── Risk adjustments (additive) ───────────────────────────────────
        foir = fd.foir or 0.0
        if foir > 0.50:
            base_rate += 0.5
            adjustments.append("+0.50% high FOIR")

        emp_years = fd.employment_stability_years or 0
        if emp_years < 2.0:
            base_rate += 0.5
            adjustments.append("+0.50% short employment")

        util = fd.credit_utilization or 0.0
        if util > 0.60:
            base_rate += 0.5
            adjustments.append("+0.50% high utilisation")

        # ── Reward adjustments (subtractive) ──────────────────────────────
        if fd.verified_income and fd.verified_income > 0:
            base_rate -= 0.25
            adjustments.append("-0.25% bank-verified income")

        delinq = fd.delinquency_count or 0
        if delinq == 0 and emp_years >= 5.0:
            base_rate -= 0.25
            adjustments.append("-0.25% clean history + stable employment")

        # Clamp rate between 9.0% and 18.0%
        final_rate = round(max(9.0, min(18.0, base_rate)), 2)

        return {
            "final_rate": final_rate,
            "base_rate": POLICY["repo_rate"] + spread,
            "adjustments": adjustments,
        }

    # ── LLM Explanation ───────────────────────────────────────────────────────

    async def _generate_explanation(self, state: SharedState, eligibility: dict) -> str:
        """
        Gemma 3 27B generates a plain-language, personalised offer explanation.
        This is the ONLY place LLM is used in offer generation.
        All numbers are injected from the deterministic policy output.
        """
        name   = state.customer_identity.name or "Customer"
        income = state.financial_data.verified_income or state.financial_data.monthly_income or 0
        cibil  = state.financial_data.bureau_score or 0
        amt    = eligibility["eligible_amount"]
        rate   = eligibility["interest_rate"]
        emi24  = self._calc_emi(amt, rate, 24)
        purpose = state.extracted_signals.loan_purpose or "your requirement"

        prompt = f"""You are a friendly Indian bank loan officer speaking to a customer during a video call.
Generate a warm, clear, 2-sentence explanation of their loan offer IN PLAIN ENGLISH (no jargon).

Customer: {name}
Monthly income: ₹{income:,.0f}
CIBIL score: {cibil}
Loan purpose: {purpose}
Approved amount: ₹{amt:,.0f}
Interest rate: {rate}% per annum
EMI for 24 months: ₹{emi24:,.0f}/month

Write ONLY the 2-sentence explanation. Be warm and encouraging. Mention their name."""

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{settings.OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model":  settings.LLM_MODEL_LARGE,
                        "prompt": prompt,
                        "stream": False,
                    },
                )
                if resp.status_code == 200:
                    return resp.json().get("response", "").strip()
        except Exception as e:
            logger.error(f"LLM explanation error: {e}")

        # Fallback: template-based explanation
        return (
            f"{name}, based on your ₹{income:,.0f} monthly income and CIBIL score of {cibil}, "
            f"you qualify for a loan of ₹{amt:,.0f} at {rate}% per annum. "
            f"Your EMI for 24 months would be ₹{emi24:,.0f}/month."
        )

    # ── Financial Calculations ────────────────────────────────────────────────

    @staticmethod
    def _calc_emi(principal: float, annual_rate: float, months: int) -> float:
        """Standard EMI calculation: P × r(1+r)^n / ((1+r)^n - 1)"""
        if principal <= 0 or months <= 0:
            return 0.0
        r = (annual_rate / 100) / 12
        if r == 0:
            return round(principal / months, 2)
        emi = principal * r * (1 + r) ** months / ((1 + r) ** months - 1)
        return round(emi, 2)

    @staticmethod
    def _calc_principal_from_emi(emi: float, annual_rate: float, months: int) -> float:
        """Reverse EMI: calculate max principal from affordable EMI."""
        if emi <= 0 or months <= 0:
            return 0.0
        r = (annual_rate / 100) / 12
        if r == 0:
            return emi * months
        principal = emi * ((1 + r) ** months - 1) / (r * (1 + r) ** months)
        return round(principal, 2)