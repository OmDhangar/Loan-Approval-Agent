"""
Risk Agent
──────────
Activated in Risk Assessment stage by SessionOrchestrator (background task).

Refactor changes:
  - Removed liveness_passed dependency from fraud_compliance component
  - Replaced with doc_authenticity_passed + doc_authenticity_score
  - Direct advance_stage() call via SessionOrchestrator (no LangGraph interrupt)
  - Fallback mock is more realistic (income-proportional score)
"""

import logging
import time
import random
from typing import Optional

from models.shared_state import SharedState, RiskBand, FinancialData
from core.redis_client import redis_client
from core.langgraph_engine import moderator_engine
from services.bureau_client import bureau_client
from services.geolocation_service import geolocation_service

logger = logging.getLogger(__name__)


class RiskAgent:

    COMPOSITE_LOW_THRESHOLD    = 70
    COMPOSITE_MEDIUM_THRESHOLD = 45
    GEO_MISMATCH_KM            = 50

    WEIGHTS = {
        "credit_score":      0.30,
        "income_stability":  0.20,
        "debt_burden":       0.15,
        "credit_behaviour":  0.15,
        "credit_appetite":   0.10,
        "fraud_compliance":  0.10,
    }

    async def handle_task(self, payload: dict):
        call_id = payload["call_id"]
        action  = payload.get("action", "full_risk_assessment")

        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            return
        state = SharedState.from_json(raw)

        if action == "full_risk_assessment":
            await self._full_assessment(call_id, state)

    async def _full_assessment(self, call_id: str, state: SharedState):
        """Run all risk signals and assign risk band."""
        report = await self._fetch_bureau_report(state)
        self._populate_state_from_bureau(state, report)

        composite, breakdown, reasons = self._compute_composite_score(state, report)
        state.financial_data.composite_risk_score = composite

        geo_ok, geo_dist = self._check_geo(state, report)
        state.financial_data.geo_distance_km = geo_dist
        if not geo_ok:
            reasons.append("geo_mismatch_gt_50km")

        risk_band, escalate = self._apply_edge_cases(composite, state, report, reasons)
        if not geo_ok:
            escalate  = True
            risk_band = RiskBand.HIGH

        state.financial_data.risk_band       = risk_band
        state.financial_data.propensity_score = round(composite / 100.0, 4)
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        await redis_client.publish(f"session:{call_id}:events", {
            "event":           "RISK_ASSESSMENT_COMPLETE",
            "risk_band":       risk_band.value,
            "bureau_score":    state.financial_data.bureau_score,
            "composite_score": composite,
            "breakdown":       breakdown,
            "call_id":         call_id,
            "ts":              time.time(),
        })

        # Geo escalation handled by Moderator via the advance_stage call below
        if not geo_ok and geo_dist is not None:
            logger.warning(f"Geo mismatch detected for {call_id}: {geo_dist}km")

        await moderator_engine.advance_stage(call_id, {
            "passed":          not escalate,
            "escalate":        escalate,
            "agent":           "risk",
            "confidence":      state.financial_data.propensity_score,
            "risk_band":       risk_band.value,
            "composite_score": composite,
            "reasons":         reasons,
        })

        logger.info(
            f"Risk [{call_id}]: band={risk_band.value}, composite={composite:.1f}, "
            f"cibil={state.financial_data.bureau_score}, geo_ok={geo_ok}"
        )

    # ── Bureau fetch ───────────────────────────────────────────────────────────

    async def _fetch_bureau_report(self, state: SharedState) -> Optional[dict]:
        report = await bureau_client.fetch_report(
            pan=state.customer_identity.pan_masked,
            name=state.customer_identity.name,
            dob=state.customer_identity.declared_dob,
        )
        if report:
            return report

        logger.warning("Bureau API unreachable — using mock fallback")
        income     = state.financial_data.monthly_income or 30_000
        base_score = 650 + int((income / 100_000) * 80)
        mock_score = min(850, max(550, base_score + random.randint(-30, 30)))

        return {
            "credit_summary": {
                "score": {"bureau": "CIBIL", "value": mock_score},
                "accounts_summary": {"total_accounts": 2, "active_accounts": 1},
                "utilization": {"credit_limit": 100_000, "current_balance": 30_000, "utilization_ratio": 0.30},
                "delinquency": {"dpd_30_plus_last_12m": 0, "dpd_60_plus_last_12m": 0, "written_off": False},
            },
            "income_profile": {
                "declared_monthly": income,
                "verified_monthly": None,
                "employment_type": state.financial_data.employment_type or "SALARIED",
                "employment_stability_years": 2.0,
                "salary_mode": "BANK_TRANSFER",
            },
            "bank_insights": {
                "avg_balance_6m": income, "monthly_inflow": income,
                "monthly_outflow": income * 0.7, "bounce_count_6m": 0,
            },
            "emi_obligations": {"existing_emi_total": 0, "foir": 0.0},
            "credit_inquiries": {"hard_inquiries_last_6m": 0},
            "fraud_flags": {"identity_fraud_alert": False, "synthetic_id_risk": False},
            "tradelines": [],
            "score_factors": ["Fallback mock data"],
            "alerts": ["Bureau API unreachable"],
        }

    def _populate_state_from_bureau(self, state: SharedState, report: Optional[dict]):
        if not report:
            return
        fd = state.financial_data
        fd.bureau_report_raw = report
        fd.bureau_score      = bureau_client.extract_score(report)
        fd.bureau_fetched_at = time.time()

        verified = bureau_client.extract_income(report)
        if verified:
            fd.verified_income            = verified
            fd.income_verification_source = "bureau_verified"
            if not fd.monthly_income:
                fd.monthly_income = verified
        elif fd.verified_income is None:
            fd.income_verification_source = "unavailable"

        fd.foir               = bureau_client.extract_foir(report)
        fd.credit_utilization = bureau_client.extract_utilization(report)

        try:
            fd.existing_emi_total = report["emi_obligations"]["existing_emi_total"]
        except (KeyError, TypeError):
            pass

        try:
            fd.delinquency_count = report["credit_summary"]["delinquency"]["dpd_30_plus_last_12m"]
        except (KeyError, TypeError):
            pass

        try:
            fd.hard_inquiries_6m = report["credit_inquiries"]["hard_inquiries_last_6m"]
        except (KeyError, TypeError):
            pass

        try:
            fd.employment_stability_years = report["income_profile"]["employment_stability_years"]
            if not fd.employment_type:
                fd.employment_type = report["income_profile"].get("employment_type")
        except (KeyError, TypeError):
            pass

        fd.fraud_flags = bureau_client.extract_fraud_flags(report)

    # ── Composite scoring ──────────────────────────────────────────────────────

    def _compute_composite_score(
        self, state: SharedState, report: Optional[dict]
    ) -> tuple[float, dict, list[str]]:
        fd      = state.financial_data
        reasons = []
        breakdown = {}

        # 1. Credit Score (30%)
        cibil = fd.bureau_score or -1
        if cibil <= 0:
            credit_sub = 50.0
            reasons.append("no_credit_score:thin_file")
        else:
            credit_sub = max(0, min(100, ((cibil - 300) / 600) * 100))
            if cibil < 650:
                reasons.append(f"low_cibil:{cibil}")
        breakdown["credit_score"] = round(credit_sub, 1)

        # 2. Income & Stability (20%)
        income    = fd.verified_income or 0
        emp_years = fd.employment_stability_years or 0
        income_norm    = min(income / 150_000, 1.0) * 60
        stability_norm = min(emp_years / 5.0, 1.0) * 40
        income_sub     = income_norm + stability_norm
        try:
            if report["income_profile"]["salary_mode"] == "CASH":
                income_sub *= 0.7
                reasons.append("cash_salary_mode")
        except (KeyError, TypeError):
            pass
        if emp_years < 1.0:
            reasons.append(f"short_employment:{emp_years}yr")
        breakdown["income_stability"] = round(income_sub, 1)

        # 3. Debt Burden / FOIR (15%)
        foir = fd.foir or 0.0
        if foir <= 0.0:
            debt_sub = 100.0
        elif foir <= 0.30:
            debt_sub = 90.0
        elif foir <= 0.50:
            debt_sub = 60.0
            reasons.append(f"moderate_foir:{foir:.2f}")
        elif foir <= 0.65:
            debt_sub = 30.0
            reasons.append(f"high_foir:{foir:.2f}")
        else:
            debt_sub = 10.0
            reasons.append(f"critical_foir:{foir:.2f}")
        breakdown["debt_burden"] = round(debt_sub, 1)

        # 4. Credit Behaviour (15%)
        util   = fd.credit_utilization or 0.0
        delinq = fd.delinquency_count or 0
        util_pts   = 50 if util <= 0.30 else (35 if util <= 0.50 else (20 if util <= 0.70 else 5))
        delinq_pts = 50 if delinq == 0 else (30 if delinq <= 1 else (15 if delinq <= 3 else 0))
        if util > 0.70:    reasons.append(f"very_high_utilization:{util:.0%}")
        if delinq > 1:     reasons.append(f"delinquency_events:{delinq}")
        breakdown["credit_behaviour"] = round(util_pts + delinq_pts, 1)

        # 5. Credit Appetite (10%)
        inquiries = fd.hard_inquiries_6m or 0
        if inquiries == 0:       appetite_sub = 100.0
        elif inquiries <= 1:     appetite_sub = 80.0
        elif inquiries <= 3:     appetite_sub = 50.0;  reasons.append(f"credit_hungry:{inquiries}")
        else:                    appetite_sub = 15.0;  reasons.append(f"excessive_inquiries:{inquiries}")
        breakdown["credit_appetite"] = round(appetite_sub, 1)

        # 6. Fraud & Compliance (10%)
        # REFACTORED: uses doc_authenticity instead of liveness_passed
        fraud_sub = 100.0
        if fd.fraud_flags:
            fraud_sub -= len(fd.fraud_flags) * 30
            for flag in fd.fraud_flags:
                reasons.append(f"fraud_flag:{flag}")

        # Document authenticity (replaces liveness check)
        doc_score = state.customer_identity.doc_authenticity_score
        if not state.customer_identity.doc_authenticity_passed:
            fraud_sub -= 25
            reasons.append("doc_authenticity_failed")
        elif doc_score < 0.8:
            fraud_sub -= 10   # Partial deduction for low-confidence doc
            reasons.append(f"doc_authenticity_low:{doc_score:.2f}")

        if not state.customer_identity.consent_given:
            fraud_sub -= 10
            reasons.append("consent_not_given")

        try:
            bounces = report["bank_insights"]["bounce_count_6m"]
            if bounces >= 3:
                fraud_sub -= 20
                reasons.append(f"high_bounces:{bounces}")
            elif bounces >= 1:
                fraud_sub -= 10
        except (KeyError, TypeError):
            pass

        fraud_sub = max(0, fraud_sub)
        breakdown["fraud_compliance"] = round(fraud_sub, 1)

        composite = sum(
            breakdown[k] * self.WEIGHTS[k] for k in self.WEIGHTS
        )
        return round(composite, 1), breakdown, reasons

    def _apply_edge_cases(
        self,
        composite: float,
        state: SharedState,
        report: Optional[dict],
        reasons: list,
    ) -> tuple[RiskBand, bool]:
        if bureau_client.has_write_off(report):
            reasons.append("OVERRIDE:written_off_account")
            return RiskBand.HIGH, True

        if state.financial_data.fraud_flags:
            reasons.append("OVERRIDE:fraud_flags_present")
            return RiskBand.HIGH, True

        try:
            dpd60 = report["credit_summary"]["delinquency"]["dpd_60_plus_last_12m"]
            if dpd60 > 0 and composite >= self.COMPOSITE_LOW_THRESHOLD:
                composite = self.COMPOSITE_MEDIUM_THRESHOLD + 5
                reasons.append(f"OVERRIDE:dpd60_cap:{dpd60}")
        except (KeyError, TypeError):
            pass

        cibil = state.financial_data.bureau_score or -1
        try:
            total_accts = report["credit_summary"]["accounts_summary"]["total_accounts"]
        except (KeyError, TypeError):
            total_accts = 0
        if cibil <= 0 or total_accts < 2:
            if composite >= self.COMPOSITE_LOW_THRESHOLD:
                composite = self.COMPOSITE_MEDIUM_THRESHOLD + 10
                reasons.append("OVERRIDE:thin_file_cap_medium")

        foir = state.financial_data.foir or 0.0
        if foir > 0.65:
            reasons.append(f"OVERRIDE:critical_foir:{foir:.2f}")
            return RiskBand.HIGH, True

        if composite >= self.COMPOSITE_LOW_THRESHOLD:
            return RiskBand.LOW, False
        elif composite >= self.COMPOSITE_MEDIUM_THRESHOLD:
            return RiskBand.MEDIUM, False
        else:
            return RiskBand.HIGH, True

    def _check_geo(self, state: SharedState, report: Optional[dict]) -> tuple[bool, Optional[float]]:
        lat = state.session_meta.geo_lat
        lng = state.session_meta.geo_lng
        observed = (lat, lng) if lat is not None and lng is not None else None
        if observed is None:
            observed = geolocation_service.resolve_ip_coords(state.session_meta.ip_address)

        declared = geolocation_service.resolve_declared_coords(report)
        if observed is None or declared is None:
            return True, None

        distance_km = geolocation_service.haversine_km(
            observed[0], observed[1], declared[0], declared[1]
        )
        return distance_km <= self.GEO_MISMATCH_KM, round(distance_km, 2)