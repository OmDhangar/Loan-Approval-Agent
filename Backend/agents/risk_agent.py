"""
Risk Agent
──────────
Activated in Stage 5 (Risk Assessment).

Steps:
  1. Fetch full bureau report via bureau client
  2. Compute composite risk score (6 weighted dimensions)
  3. Geo-mismatch check (device geo vs. declared city)
  4. Handle edge cases (thin file, write-off, high income + poor behaviour)
  5. Assign risk band: LOW / MEDIUM / HIGH
  6. Report to Moderator — HIGH triggers human escalation
"""

import logging
import math
import time
import random
from typing import Optional

import httpx

from models.shared_state import SharedState, RiskBand, FinancialData
from core.redis_client import redis_client
from core.config import settings
from core.langgraph_engine import moderator_engine
from services.bureau_client import bureau_client
from services.geolocation_service import geolocation_service

logger = logging.getLogger(__name__)


class RiskAgent:

    # ── Risk band thresholds on composite score (0-100) ───────────────────────
    COMPOSITE_LOW_THRESHOLD    = 70    # score >= 70 → LOW
    COMPOSITE_MEDIUM_THRESHOLD = 45    # score 45-69 → MEDIUM
    #                                  # score < 45  → HIGH

    # Geo mismatch threshold (km)
    GEO_MISMATCH_KM = 50

    # ── Weight distribution for composite score ──────────────────────────────
    WEIGHTS = {
        "credit_score":      0.30,   # CIBIL score
        "income_stability":  0.20,   # Income level + employment tenure
        "debt_burden":       0.15,   # FOIR + existing EMIs vs income
        "credit_behaviour":  0.15,   # Utilisation + delinquency + payment history
        "credit_appetite":   0.10,   # Recent hard inquiries
        "fraud_compliance":  0.10,   # Fraud flags + geo mismatch + liveness
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
        """Run all risk signals and assign risk band using composite scoring."""

        # 1. Fetch full bureau report
        report = await self._fetch_bureau_report(state)

        # 2. Populate state from bureau data
        self._populate_state_from_bureau(state, report)

        # 3. Compute composite risk score (0-100)
        composite, breakdown, reasons = self._compute_composite_score(state, report)
        state.financial_data.composite_risk_score = composite

        # 4. Geo mismatch check
        geo_ok, geo_dist = self._check_geo(state, report)
        state.financial_data.geo_distance_km = geo_dist
        if not geo_ok:
            reasons.append("geo_mismatch_gt_50km")

        # 5. Edge case overrides
        risk_band, escalate = self._apply_edge_cases(composite, state, report, reasons)
        if not geo_ok:
            escalate = True
            risk_band = RiskBand.HIGH
        state.financial_data.risk_band = risk_band

        # 6. Store propensity score (backward-compatible)
        state.financial_data.propensity_score = round(composite / 100.0, 4)

        # 7. Persist state
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        # 8. Notify frontend
        await redis_client.publish(f"session:{call_id}:events", {
            "event":            "RISK_ASSESSMENT_COMPLETE",
            "risk_band":        risk_band.value,
            "bureau_score":     state.financial_data.bureau_score,
            "composite_score":  composite,
            "breakdown":        breakdown,
            "call_id":          call_id,
        })

        # 9. Report to Moderator
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
            f"Risk assessment [{call_id}]: band={risk_band.value}, "
            f"composite={composite:.1f}, "
            f"cibil={state.financial_data.bureau_score}, "
            f"foir={state.financial_data.foir}, "
            f"geo_ok={geo_ok}"
        )

    # ── Bureau Report Fetch ───────────────────────────────────────────────────

    async def _fetch_bureau_report(self, state: SharedState) -> Optional[dict]:
        """
        Fetch full bureau report from the mock/real bureau API.
        Falls back to a minimal mock if the API is unreachable.
        """
        report = await bureau_client.fetch_report(
            pan=state.customer_identity.pan_masked,
            name=state.customer_identity.name,
            dob=state.customer_identity.declared_dob,
        )

        if report:
            return report

        # Fallback: generate minimal mock data from what we have
        logger.warning("Bureau API unreachable, using fallback mock")
        income = state.financial_data.monthly_income or 30000
        base_score = 650 + int((income / 100000) * 80)
        mock_score = min(850, max(550, base_score + random.randint(-30, 30)))

        return {
            "credit_summary": {
                "score": {"bureau": "CIBIL", "value": mock_score, "range": "300-900", "risk_band": "MEDIUM"},
                "accounts_summary": {"total_accounts": 1, "active_accounts": 1, "closed_accounts": 0, "secured_accounts": 0, "unsecured_accounts": 1, "oldest_account_months": 12},
                "utilization": {"credit_limit": 100000, "current_balance": 30000, "utilization_ratio": 0.30},
                "delinquency": {"dpd_30_plus_last_12m": 0, "dpd_60_plus_last_12m": 0, "dpd_90_plus_ever": 0, "written_off": False, "max_dpd_last_24m": 0},
            },
            "income_profile": {
                "declared_monthly": income, "verified_monthly": None,
                "employment_type": state.financial_data.employment_type or "SALARIED",
                "employment_stability_years": 2.0, "salary_mode": "BANK_TRANSFER",
            },
            "bank_insights": {"avg_balance_6m": income, "monthly_inflow": income, "monthly_outflow": income * 0.7, "cash_flow_ratio": 1.3, "bounce_count_6m": 0, "salary_credits_regularity": 0.8},
            "emi_obligations": {"existing_emi_total": 0, "existing_loan_count": 0, "foir": 0.0},
            "credit_inquiries": {"hard_inquiries_last_6m": 0, "hard_inquiries_last_12m": 0},
            "fraud_flags": {"identity_fraud_alert": False, "synthetic_id_risk": False, "device_velocity_alert": False, "pan_aadhaar_mismatch": False},
            "tradelines": [],
            "score_factors": ["Fallback mock data used"],
            "alerts": ["Bureau API unreachable — mock data in use"],
        }

    # ── Populate State from Bureau ────────────────────────────────────────────

    def _populate_state_from_bureau(self, state: SharedState, report: Optional[dict]):
        """Extract key fields from bureau report into SharedState."""
        if not report:
            return

        fd = state.financial_data
        fd.bureau_report_raw = report
        fd.bureau_score = bureau_client.extract_score(report)
        fd.bureau_fetched_at = time.time()

        # Income
        verified = bureau_client.extract_income(report)
        if verified:
            fd.verified_income = verified
            fd.income_verification_source = "bureau_verified"
            if not fd.monthly_income:
                fd.monthly_income = verified
        elif fd.verified_income is None:
            fd.income_verification_source = "unavailable"

        # Debt burden
        fd.foir = bureau_client.extract_foir(report)
        fd.credit_utilization = bureau_client.extract_utilization(report)

        try:
            fd.existing_emi_total = report["emi_obligations"]["existing_emi_total"]
        except (KeyError, TypeError):
            pass

        # Delinquency
        try:
            fd.delinquency_count = report["credit_summary"]["delinquency"]["dpd_30_plus_last_12m"]
        except (KeyError, TypeError):
            pass

        # Inquiries
        try:
            fd.hard_inquiries_6m = report["credit_inquiries"]["hard_inquiries_last_6m"]
        except (KeyError, TypeError):
            pass

        # Employment
        try:
            fd.employment_stability_years = report["income_profile"]["employment_stability_years"]
            if not fd.employment_type:
                fd.employment_type = report["income_profile"].get("employment_type")
            if not fd.employer_name:
                fd.employer_name = report["income_profile"].get("employer_name")
        except (KeyError, TypeError):
            pass

        # Fraud
        fd.fraud_flags = bureau_client.extract_fraud_flags(report)

    # ── Composite Risk Score ──────────────────────────────────────────────────

    def _compute_composite_score(
        self, state: SharedState, report: Optional[dict]
    ) -> tuple[float, dict, list[str]]:
        """
        Compute a weighted composite risk score (0-100).
        Higher = lower risk = better.

        Returns: (score, breakdown_dict, reasons_list)
        """
        fd = state.financial_data
        reasons = []
        breakdown = {}

        # ── 1. Credit Score Component (30%) ──────────────────────────────
        cibil = fd.bureau_score or -1
        if cibil <= 0:
            # No score / thin file → neutral 50
            credit_sub = 50.0
            reasons.append("no_credit_score:thin_file")
        else:
            credit_sub = max(0, min(100, ((cibil - 300) / 600) * 100))
            if cibil < 650:
                reasons.append(f"low_cibil:{cibil}")
        breakdown["credit_score"] = round(credit_sub, 1)

        # ── 2. Income & Stability Component (20%) ────────────────────────
        income = fd.verified_income or 0
        emp_years = fd.employment_stability_years or 0

        # Income sub-score: normalised against 150K (saturation point)
        income_norm = min(income / 150_000, 1.0) * 60  # Max 60 from income

        # Stability sub-score: 1yr+ = good, 5yr+ = excellent
        stability_norm = min(emp_years / 5.0, 1.0) * 40  # Max 40 from stability

        income_sub = income_norm + stability_norm

        # Salary mode penalty
        try:
            salary_mode = report["income_profile"]["salary_mode"]
            if salary_mode == "CASH":
                income_sub *= 0.7   # 30% penalty for cash income
                reasons.append("cash_salary_mode")
        except (KeyError, TypeError):
            pass

        if emp_years < 1.0:
            reasons.append(f"short_employment:{emp_years}yr")
        breakdown["income_stability"] = round(income_sub, 1)

        # ── 3. Debt Burden / FOIR Component (15%) ────────────────────────
        foir = fd.foir or 0.0
        # FOIR < 0.30 = excellent, 0.30-0.50 = ok, > 0.50 = stressed, > 0.65 = danger
        if foir <= 0.0:
            debt_sub = 100.0   # No obligations = best
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

        # ── 4. Credit Behaviour Component (15%) ──────────────────────────
        util = fd.credit_utilization or 0.0
        delinq = fd.delinquency_count or 0

        # Utilisation sub (0-50 points)
        if util <= 0.30:
            util_pts = 50.0
        elif util <= 0.50:
            util_pts = 35.0
        elif util <= 0.70:
            util_pts = 20.0
            reasons.append(f"high_utilization:{util:.0%}")
        else:
            util_pts = 5.0
            reasons.append(f"very_high_utilization:{util:.0%}")

        # Delinquency sub (0-50 points)
        if delinq == 0:
            delinq_pts = 50.0
        elif delinq <= 1:
            delinq_pts = 30.0
            reasons.append(f"delinquency_events:{delinq}")
        elif delinq <= 3:
            delinq_pts = 15.0
            reasons.append(f"multiple_delinquencies:{delinq}")
        else:
            delinq_pts = 0.0
            reasons.append(f"severe_delinquencies:{delinq}")

        behaviour_sub = util_pts + delinq_pts
        breakdown["credit_behaviour"] = round(behaviour_sub, 1)

        # ── 5. Credit Appetite Component (10%) ───────────────────────────
        inquiries = fd.hard_inquiries_6m or 0
        if inquiries == 0:
            appetite_sub = 100.0
        elif inquiries <= 1:
            appetite_sub = 80.0
        elif inquiries <= 3:
            appetite_sub = 50.0
            reasons.append(f"credit_hungry:{inquiries}_inquiries_6m")
        else:
            appetite_sub = 15.0
            reasons.append(f"excessive_inquiries:{inquiries}_in_6m")
        breakdown["credit_appetite"] = round(appetite_sub, 1)

        # ── 6. Fraud & Compliance Component (10%) ────────────────────────
        fraud_sub = 100.0
        if fd.fraud_flags:
            fraud_sub -= len(fd.fraud_flags) * 30
            for flag in fd.fraud_flags:
                reasons.append(f"fraud_flag:{flag}")

        if not state.customer_identity.liveness_passed:
            fraud_sub -= 20
            reasons.append("liveness_failed")

        if state.customer_identity.consent_given:
            fraud_sub += 0  # No bonus, just no penalty
        else:
            fraud_sub -= 10
            reasons.append("consent_not_given")

        # Bounces from bank insights
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

        # ── Weighted composite ───────────────────────────────────────────
        composite = (
            breakdown["credit_score"]     * self.WEIGHTS["credit_score"]
            + breakdown["income_stability"] * self.WEIGHTS["income_stability"]
            + breakdown["debt_burden"]      * self.WEIGHTS["debt_burden"]
            + breakdown["credit_behaviour"] * self.WEIGHTS["credit_behaviour"]
            + breakdown["credit_appetite"]  * self.WEIGHTS["credit_appetite"]
            + breakdown["fraud_compliance"] * self.WEIGHTS["fraud_compliance"]
        )

        return round(composite, 1), breakdown, reasons

    # ── Edge Case Handling ────────────────────────────────────────────────────

    def _apply_edge_cases(
        self,
        composite: float,
        state: SharedState,
        report: Optional[dict],
        reasons: list,
    ) -> tuple[RiskBand, bool]:
        """
        Apply edge-case overrides that trump the composite score.
        Returns (risk_band, should_escalate).
        """
        escalate = False

        # Override 1: Written-off account → auto-HIGH
        if bureau_client.has_write_off(report):
            reasons.append("OVERRIDE:written_off_account")
            return RiskBand.HIGH, True

        # Override 2: Any fraud flag → auto-HIGH
        if state.financial_data.fraud_flags:
            reasons.append("OVERRIDE:fraud_flags_present")
            return RiskBand.HIGH, True

        # Override 3: DPD 60+ in last 12m → cap at MEDIUM minimum
        try:
            dpd60 = report["credit_summary"]["delinquency"]["dpd_60_plus_last_12m"]
            if dpd60 > 0 and composite >= self.COMPOSITE_LOW_THRESHOLD:
                composite = self.COMPOSITE_MEDIUM_THRESHOLD + 5  # Force into MEDIUM
                reasons.append(f"OVERRIDE:dpd60_cap:{dpd60}")
        except (KeyError, TypeError):
            pass

        # Override 4: Thin file (no score or < 2 accounts) → cap at MEDIUM
        cibil = state.financial_data.bureau_score or -1
        try:
            total_accts = report["credit_summary"]["accounts_summary"]["total_accounts"]
        except (KeyError, TypeError):
            total_accts = 0

        if cibil <= 0 or total_accts < 2:
            if composite >= self.COMPOSITE_LOW_THRESHOLD:
                composite = self.COMPOSITE_MEDIUM_THRESHOLD + 10
                reasons.append("OVERRIDE:thin_file_cap_medium")

        # Override 5: FOIR > 0.65 → auto-HIGH (over-leveraged)
        foir = state.financial_data.foir or 0.0
        if foir > 0.65:
            reasons.append(f"OVERRIDE:critical_foir:{foir:.2f}")
            return RiskBand.HIGH, True

        # Standard band assignment
        if composite >= self.COMPOSITE_LOW_THRESHOLD:
            band = RiskBand.LOW
        elif composite >= self.COMPOSITE_MEDIUM_THRESHOLD:
            band = RiskBand.MEDIUM
        else:
            band = RiskBand.HIGH
            escalate = True

        return band, escalate

    # ── Geo Check (unchanged from original) ───────────────────────────────────

    def _check_geo(self, state: SharedState, report: Optional[dict]) -> tuple[bool, Optional[float]]:
        """
        Cross-verify device geo (lat/lng captured at session start)
        vs. declared city (derived from income/employment data).
        MVP: returns (True, 0) — geo mismatch detection requires IP-to-city DB.
        Production: MaxMind GeoIP2 + Haversine formula.
        """
        observed = None
        lat = state.session_meta.geo_lat
        lng = state.session_meta.geo_lng
        if lat is not None and lng is not None:
            observed = (lat, lng)
        if observed is None:
            observed = geolocation_service.resolve_ip_coords(state.session_meta.ip_address)

        declared = geolocation_service.resolve_declared_coords(report)
        if observed is None or declared is None:
            return True, None

        distance_km = geolocation_service.haversine_km(
            observed[0],
            observed[1],
            declared[0],
            declared[1],
        )
        return distance_km <= self.GEO_MISMATCH_KM, round(distance_km, 2)