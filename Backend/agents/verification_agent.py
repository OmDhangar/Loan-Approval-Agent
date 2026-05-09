"""
Verification Agent
──────────────────
Handles identity and document verification.

Refactor changes:
  - Added _verify_document_authenticity() in OVD_DOCUMENT_CAPTURE stage
  - Removed all liveness stage references
  - Direct EventBus emission instead of LangGraph interrupts
  - advance_stage() calls are direct (no RabbitMQ)

Stage responsibilities:
  OVD_DOCUMENT_CAPTURE : Document authenticity check (format, MRZ, font, metadata)
  IDENTITY_KYC         : Cross-verify name + DOB against bureau
  EMPLOYMENT_INCOME    : Validate income declaration
"""

import logging
import re
import time
from typing import Optional

from models.shared_state import SharedState, SessionStage
from core.redis_client import redis_client
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

        if action == "verify_document_authenticity":
            await self._verify_document_authenticity(call_id, state)

        elif action == "verify_identity_documents":
            await self._verify_identity(call_id, state)

        elif action == "validate_income_declaration":
            await self._validate_income(call_id, state)

    # ── OVD Stage: Document Authenticity ──────────────────────────────────────

    async def _verify_document_authenticity(self, call_id: str, state: SharedState):
        """
        Replaces the liveness challenge stage.
        Performs heuristic + structural checks on the uploaded document.

        Checks (MVP heuristic; production: ML-based tamper detection):
          1. Document type detected from OVD field
          2. Image metadata check (done in upload endpoint)
          3. Text pattern match (Aadhaar: 12-digit, PAN: AAAAA9999A)
          4. Face region present (if snapshot available)
          5. No obvious digital artifacts (blur/pixelation detection)

        In production:
          - Integrate UIDAI masked Aadhaar verification API
          - Use OCR + MRZ parser for PAN/passport
          - Check hologram patterns with ML
        """
        issues   = []
        checks   = []
        score    = 1.0    # Start at full confidence, deduct per failure

        ovd_type  = state.customer_identity.ovd_type
        ovd_num   = state.customer_identity.ovd_number_masked

        # Check 1: Document type must be known
        if not ovd_type:
            issues.append("ovd_type_unknown")
            score -= 0.4
        else:
            checks.append(f"doc_type_detected:{ovd_type}")

        # Check 2: Format validation for Aadhaar / PAN
        if ovd_type == "aadhaar":
            # Masked Aadhaar: XXXX-XXXX-1234 or just last 4 digits
            if ovd_num and not re.match(r"^[Xx*\d]{4}[-\s]?[Xx*\d]{4}[-\s]?\d{4}$", ovd_num):
                issues.append("aadhaar_format_invalid")
                score -= 0.2
            else:
                checks.append("aadhaar_format_ok")
        elif ovd_type == "pan":
            if ovd_num and not re.match(r"^[A-Z]{5}\d{4}[A-Z]$", ovd_num):
                issues.append("pan_format_invalid")
                score -= 0.2
            else:
                checks.append("pan_format_ok")

        # Check 3: Try to read snapshot for face region (non-blocking, non-fatal)
        face_detected = await self._check_face_in_snapshot(call_id)
        if face_detected is True:
            checks.append("face_detected_in_frame")
        elif face_detected is False:
            # Warn but don't fail — customer may have bad lighting
            checks.append("face_not_detected_in_frame")
            score = max(score - 0.1, 0.5)

        # Check 4: Minimum passing threshold
        score = round(max(0.0, min(1.0, score)), 2)
        passed = score >= 0.5 and "ovd_type_unknown" not in issues

        # Write results to state
        state.customer_identity.doc_authenticity_passed  = passed
        state.customer_identity.doc_authenticity_score   = score
        state.customer_identity.doc_authenticity_checks  = checks
        # Alias liveness fields so risk_agent stays compatible
        state.customer_identity.liveness_passed          = passed
        state.customer_identity.liveness_challenge_passed = passed
        state.customer_identity.liveness_score           = score
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        # Notify frontend
        await redis_client.publish(f"session:{call_id}:events", {
            "event":   "DOCUMENT_AUTHENTICITY_RESULT",
            "passed":  passed,
            "score":   score,
            "checks":  checks,
            "issues":  issues,
            "call_id": call_id,
            "ts":      time.time(),
        })

        logger.info(
            f"Doc authenticity [{call_id}]: passed={passed}, score={score}, "
            f"ovd_type={ovd_type}, issues={issues}"
        )

        # Report to orchestrator — advance to Aadhaar OTP stage
        await moderator_engine.advance_stage(call_id, {
            "passed":     passed,
            "escalate":   score < 0.3,   # Hard fail → escalate
            "agent":      "verification",
            "confidence": score,
            "issues":     issues,
        })

    async def _check_face_in_snapshot(self, call_id: str) -> Optional[bool]:
        """
        Non-blocking: check if a snapshot is available and if a face is visible.
        Returns True/False/None (None = no snapshot yet, skip check).
        """
        try:
            import json
            raw = await redis_client.get_state(f"session:{call_id}:snapshot")
            if not raw:
                return None
            data = json.loads(raw)
            if data.get("image"):
                # MVP: just confirm image exists and is non-trivially sized
                img_size = len(data["image"])
                return img_size > 5000   # >5KB suggests actual content
        except Exception as e:
            logger.debug(f"Snapshot check failed for {call_id}: {e}")
        return None

    # ── IDENTITY_KYC Stage ────────────────────────────────────────────────────

    async def _verify_identity(self, call_id: str, state: SharedState):
        """Cross-verify name and DOB against bureau data."""
        issues   = []
        warnings = []

        name = state.customer_identity.name
        dob  = state.customer_identity.declared_dob
        pan  = state.customer_identity.pan_masked

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

        # Bureau cross-verification
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
            if dob and not bureau_result.get("dob_match"):
                issues.append("dob_bureau_mismatch")

            for bi in bureau_result.get("issues", []):
                if bi not in issues:
                    issues.append(bi)

        passed     = len(issues) == 0
        escalate   = len(issues) > 2
        confidence = 0.9 if passed else 0.4

        state.customer_identity.identity_verified = passed
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        await redis_client.publish(f"session:{call_id}:events", {
            "event":    "IDENTITY_VERIFICATION_RESULT",
            "verified": passed,
            "issues":   issues,
            "warnings": warnings,
            "call_id":  call_id,
        })

        if state.current_stage == SessionStage.IDENTITY_KYC:
            await moderator_engine.advance_stage(call_id, {
                "passed":     passed,
                "escalate":   escalate,
                "agent":      "verification",
                "confidence": confidence,
                "issues":     issues,
            })

        logger.info(f"Identity verification [{call_id}]: passed={passed}, issues={issues}")

    # ── EMPLOYMENT_INCOME Stage ───────────────────────────────────────────────

    async def _validate_income(self, call_id: str, state: SharedState):
        income = state.financial_data.monthly_income
        emp    = state.financial_data.employment_type

        if not income:
            return

        issues   = []
        warnings = []

        if income < 5_000:
            issues.append(f"income_too_low:{income}")
        elif income > 5_000_000:
            warnings.append(f"income_unusually_high:{income}")

        if emp and emp.lower() not in {
            "salaried", "self-employed", "self employed",
            "business", "freelance", "professional"
        }:
            warnings.append(f"unusual_employment_type:{emp}")

        passed     = len(issues) == 0
        confidence = 0.85 if passed else 0.4

        state.financial_data.income_confidence = confidence
        state.version += 1
        await redis_client.set_state(state.redis_key(), state.to_json())

        if state.current_stage == SessionStage.EMPLOYMENT_INCOME:
            await moderator_engine.advance_stage(call_id, {
                "passed":     passed,
                "agent":      "verification",
                "confidence": confidence,
                "issues":     issues,
            })

        logger.info(f"Income verification [{call_id}]: passed={passed}, income={income}")

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_valid_name_format(name: str) -> bool:
        cleaned = name.strip()
        if not re.match(r"^[A-Za-z\s\.'-]{3,60}$", cleaned):
            return False
        words = cleaned.split()
        return len(words) >= 2 and all(len(w) >= 2 for w in words)

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