"""
Verification Agent
──────────────────
Activated in Stage 2 (Identity & KYC) and Stage 3 (Employment & Income).

Stage 2 responsibilities:
  - Cross-verify name and DOB against bureau data (mock_bureau)
  - Validate Aadhaar/PAN format from STT transcript
  - Trigger UIDAI masked Aadhaar check (mock for MVP)

Stage 3 responsibilities:
  - Validate income declaration against bureau-verified income
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
from services.bureau_client import bureau_client

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

        name = state.customer_identity.name
        dob  = state.customer_identity.declared_dob
        pan  = state.customer_identity.pan_masked

        # 1. Basic format validation
        if not name or len(name.strip()) < 3:
            issues.append("name_missing")
        elif not self._is_valid_name_format(name):
            issues.append("name_invalid_format")

        if not dob:
            issues.append("dob_missing")
        else:
            age = self._calc_age(dob)
            if age is not None and (age < 21 or age > 65):
                issues.append(f"age_out_of_range:{age}")

        # 2. Aadhaar format check
        if state.customer_identity.aadhaar_masked:
            if not self._valid_aadhaar_format(state.customer_identity.aadhaar_masked):
                warnings.append("aadhaar_format_invalid")

        # 3. Bureau-based cross-verification (the real verification)
        if name and len(name.strip()) >= 3:
            bureau_result = await bureau_client.verify_identity(
                declared_name=name,
                declared_dob=dob,
                pan=pan,
            )

            if bureau_result.get("bureau_name"):
                state.customer_identity.bureau_verified_name = bureau_result["bureau_name"]

            if not bureau_result.get("name_match"):
                issues.append("name_bureau_mismatch")
                if bureau_result.get("bureau_name"):
                    logger.warning(
                        f"Name mismatch [{call_id}]: "
                        f"declared='{name}' vs bureau='{bureau_result['bureau_name']}' "
                        f"(confidence={bureau_result.get('name_confidence', 0)})"
                    )

            if dob and not bureau_result.get("dob_match"):
                issues.append("dob_bureau_mismatch")
                if bureau_result.get("bureau_dob"):
                    logger.warning(
                        f"DOB mismatch [{call_id}]: "
                        f"declared='{dob}' vs bureau='{bureau_result['bureau_dob']}'"
                    )

            if bureau_result.get("verified"):
                logger.info(f"Identity VERIFIED against bureau [{call_id}]: {name}")
            else:
                logger.info(
                    f"Identity NOT verified [{call_id}]: issues={bureau_result.get('issues', [])}"
                )

            # Merge bureau issues into our issues list
            for bi in bureau_result.get("issues", []):
                if bi not in issues:
                    issues.append(bi)

        passed   = len(issues) == 0
        escalate = len(issues) > 2   # Multiple failures → escalate
        confidence = 0.9 if passed else 0.4

        # Update state
        state.customer_identity.liveness_passed = passed
        state.customer_identity.identity_verified = passed
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        # Report to Moderator
        if state.current_stage == SessionStage.IDENTITY_KYC:
            await moderator_engine.advance_stage(call_id, {
                "passed":     passed,
                "escalate":   escalate,
                "agent":      "verification",
                "confidence": confidence,
                "issues":     issues,
                "warnings":   warnings,
            })

        # Notify frontend
        await redis_client.publish(f"session:{call_id}:events", {
            "event":    "IDENTITY_VERIFICATION_RESULT",
            "verified": passed,
            "issues":   issues,
            "warnings": warnings,
            "call_id":  call_id,
        })

        logger.info(f"Identity verification [{call_id}]: passed={passed}, issues={issues}")

    # ── Stage 3: Income validation ─────────────────────────────────────────────

    async def _validate_income(self, call_id: str, state: SharedState):
        income = state.financial_data.monthly_income
        emp    = state.financial_data.employment_type

        if not income:
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

        # Report to Moderator
        if state.current_stage == SessionStage.EMPLOYMENT_INCOME:
            await moderator_engine.advance_stage(call_id, {
                "passed":     passed,
                "agent":      "verification",
                "confidence": confidence,
                "issues":     issues,
                "warnings":   warnings,
            })

        logger.info(f"Income verification [{call_id}]: passed={passed}, income={income}")

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_valid_name_format(name: str) -> bool:
        """
        Basic format validation for a name.
        Must be alphabetic characters with spaces, at least 2 words for KYC.
        Rejects obvious non-names (acronyms, single words, etc.)
        The actual name correctness is validated against bureau data.
        """
        cleaned = name.strip()
        # Must match alphabetic pattern
        if not re.match(r"^[A-Za-z\s\.'-]{3,60}$", cleaned):
            return False
        # Must have at least 2 words (first + last name for KYC)
        words = cleaned.split()
        if len(words) < 2:
            return False
        # Each word must be at least 2 characters
        if any(len(w) < 2 for w in words):
            return False
        return True

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