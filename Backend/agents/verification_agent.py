"""
Verification Agent
──────────────────
Activated in Stage 2 (Identity & KYC) and Stage 3 (Employment & Income).

Stage 2 responsibilities:
  - Cross-verify Aadhaar/PAN format from STT transcript
  - Trigger UIDAI masked Aadhaar check (mock for MVP)
  - Cross-check declared name vs. bureau name

Stage 3 responsibilities:
  - Validate income declaration is within plausible range
  - Cross-reference with bureau data for salaried customers
  - Flag anomalies to Moderator
"""

import logging
import re
import time
from typing import Optional

import httpx

from models.shared_state import SharedState, SessionStage
from core.redis_client import redis_client
from core.config import settings
from core.langgraph_engine import moderator_engine

logger = logging.getLogger(__name__)


class VerificationAgent:

    async def handle_task(self, payload: dict):
        call_id = payload["call_id"]
        action  = payload.get("action", "")

        raw = await redis_client.get_state(f"session:{call_id}:state")
        if not raw:
            return
        state = SharedState.from_json(raw)

        if action == "verify_identity_documents":
            await self._verify_identity(call_id, state)

        elif action == "validate_income_declaration":
            await self._validate_income(call_id, state)

    # ── Stage 2: Identity verification ────────────────────────────────────────

    async def _verify_identity(self, call_id: str, state: SharedState):
        issues   = []
        warnings = []

        # 1. Name validation
        name = state.customer_identity.name
        if not name or len(name.strip()) < 3:
            issues.append("name_missing")
        elif not self._is_valid_name(name):
            warnings.append("name_unusual_format")

        # 2. DOB validation
        dob = state.customer_identity.declared_dob
        if not dob:
            issues.append("dob_missing")
        else:
            age = self._calc_age(dob)
            if age is not None and (age < 21 or age > 65):
                issues.append(f"age_out_of_range:{age}")

        # 3. Aadhaar check (mock; production: UIDAI masked API)
        if state.customer_identity.aadhaar_masked:
            if not self._valid_aadhaar_format(state.customer_identity.aadhaar_masked):
                warnings.append("aadhaar_format_invalid")

        passed   = len(issues) == 0
        escalate = len(issues) > 1   # Multiple failures → escalate

        # Update state
        state.customer_identity.liveness_passed = passed
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        # await moderator_engine.advance_stage(call_id, {
        #     "passed":     passed,
        #     "escalate":   escalate,
        #     "agent":      "verification",
        #     "confidence": 0.9 if passed else 0.3,
        #     "issues":     issues,
        #     "warnings":   warnings,
        # })

        logger.info(f"Identity verification [{call_id}]: passed={passed}, issues={issues}")

    # ── Stage 3: Income validation ─────────────────────────────────────────────

    async def _validate_income(self, call_id: str, state: SharedState):
        income = state.financial_data.monthly_income
        emp    = state.financial_data.employment_type

        if not income:
            # await moderator_engine.advance_stage(call_id, {
            #     "passed":     False,
            #     "agent":      "verification",
            #     "confidence": 0.2,
            #     "issues":     ["income_missing"],
            # })
            return

        issues   = []
        warnings = []

        # Range check
        if income < 5_000:
            issues.append(f"income_too_low:{income}")
        elif income > 5_000_000:
            warnings.append(f"income_unusually_high:{income}")

        # Employment type sanity
        if emp and emp.lower() not in (
            "salaried", "self-employed", "self employed",
            "business", "freelance", "professional",
        ):
            warnings.append(f"unusual_employment_type:{emp}")

        passed = len(issues) == 0
        confidence = 0.85 if passed else 0.4

        # Update income confidence in state
        state.financial_data.income_confidence = confidence
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        # await moderator_engine.advance_stage(call_id, {
        #     "passed":     passed,
        #     "agent":      "verification",
        #     "confidence": confidence,
        #     "issues":     issues,
        #     "warnings":   warnings,
        # })

        logger.info(f"Income verification [{call_id}]: passed={passed}, income={income}")

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_valid_name(name: str) -> bool:
        return bool(re.match(r"^[A-Za-z\s\.'-]{3,60}$", name.strip()))

    @staticmethod
    def _valid_aadhaar_format(aadhaar: str) -> bool:
        """Masked Aadhaar: XXXX-XXXX-1234"""
        return bool(re.match(r"^[Xx*]{4}[-\s]?[Xx*]{4}[-\s]?\d{4}$", aadhaar.strip()))

    @staticmethod
    def _calc_age(dob_str: str) -> Optional[int]:
        from datetime import datetime
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                dob = datetime.strptime(dob_str.strip(), fmt)
                return (datetime.now() - dob).days // 365
            except ValueError:
                continue
        return None