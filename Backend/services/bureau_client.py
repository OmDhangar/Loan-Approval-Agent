"""
Bureau Client Service
─────────────────────
Async HTTP client that fetches credit bureau reports from the mock (or real) API.
Used by the Risk Agent to get full bureau data for risk scoring.

Features:
  - Retry logic (3 attempts with exponential backoff)
  - Timeout handling (10s)
  - Graceful fallback if bureau is unreachable
  - Typed response parsing
"""

import logging
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class BureauClient:
    """Async client for the credit bureau API."""

    MAX_RETRIES = 3
    RETRY_DELAYS = [1, 2, 4]   # seconds (exponential backoff)
    TIMEOUT = 10               # seconds

    def __init__(self):
        self.base_url = settings.BUREAU_API_URL
        self.api_key = settings.BUREAU_API_KEY

    async def fetch_report(
        self,
        pan: Optional[str] = None,
        name: Optional[str] = None,
        dob: Optional[str] = None,
        consent_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Fetch a full bureau report.

        Args:
            pan: Customer PAN number (primary lookup key).
            name: Customer name (fallback lookup).
            dob: Date of birth (for validation).
            consent_id: Consent reference ID.

        Returns:
            Full bureau report dict, or None if all retries fail.
        """
        params = {}
        if pan:
            params["pan"] = pan
        elif name:
            params["name"] = name
        else:
            logger.warning("Bureau fetch called with no PAN or name")
            return None

        url = f"{self.base_url}/report"
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                    resp = await client.get(
                        url,
                        params=params,
                        headers={
                            "X-API-Key": self.api_key,
                            "X-Consent-ID": consent_id or "",
                        },
                    )

                    if resp.status_code == 200:
                        report = resp.json()
                        logger.info(
                            f"Bureau report fetched: "
                            f"pan={pan or 'N/A'}, "
                            f"cibil={report.get('credit_summary', {}).get('score', {}).get('value', '?')}"
                        )
                        return report

                    elif resp.status_code == 400:
                        logger.error(f"Bureau API bad request: {resp.text}")
                        return None  # Don't retry on 400

                    else:
                        last_error = f"HTTP {resp.status_code}: {resp.text}"
                        logger.warning(
                            f"Bureau API attempt {attempt+1}/{self.MAX_RETRIES} "
                            f"failed: {last_error}"
                        )

            except httpx.TimeoutException:
                last_error = "Request timed out"
                logger.warning(
                    f"Bureau API attempt {attempt+1}/{self.MAX_RETRIES} "
                    f"timed out after {self.TIMEOUT}s"
                )

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Bureau API attempt {attempt+1}/{self.MAX_RETRIES} "
                    f"error: {e}"
                )

            # Wait before retry (except on last attempt)
            if attempt < self.MAX_RETRIES - 1:
                import asyncio
                await asyncio.sleep(self.RETRY_DELAYS[attempt])

        logger.error(f"Bureau API failed after {self.MAX_RETRIES} attempts: {last_error}")
        return None

    def extract_score(self, report: dict) -> int:
        """Extract CIBIL score from bureau report. Returns -1 for thin file."""
        try:
            return report["credit_summary"]["score"]["value"]
        except (KeyError, TypeError):
            return -1

    def extract_income(self, report: dict) -> Optional[float]:
        """Extract verified monthly income only (zero-trust)."""
        try:
            ip = report["income_profile"]
            return ip.get("verified_monthly")
        except (KeyError, TypeError):
            return None

    def extract_foir(self, report: dict) -> float:
        """Extract Fixed Obligation to Income Ratio."""
        try:
            return report["emi_obligations"]["foir"]
        except (KeyError, TypeError):
            return 0.0

    def extract_utilization(self, report: dict) -> float:
        """Extract credit utilization ratio."""
        try:
            return report["credit_summary"]["utilization"]["utilization_ratio"]
        except (KeyError, TypeError):
            return 0.0

    def has_write_off(self, report: dict) -> bool:
        """Check if customer has any written-off accounts."""
        try:
            return report["credit_summary"]["delinquency"]["written_off"]
        except (KeyError, TypeError):
            return False

    def extract_fraud_flags(self, report: dict) -> list:
        """Extract active fraud flags as a list of strings."""
        flags = []
        try:
            ff = report["fraud_flags"]
            if ff.get("identity_fraud_alert"):
                flags.append("identity_fraud_alert")
            if ff.get("synthetic_id_risk"):
                flags.append("synthetic_id_risk")
            if ff.get("device_velocity_alert"):
                flags.append("device_velocity_alert")
            if ff.get("pan_aadhaar_mismatch"):
                flags.append("pan_aadhaar_mismatch")
        except (KeyError, TypeError):
            pass
        return flags

    # ── Identity Verification against Bureau Data ─────────────────────────────

    def extract_bureau_name(self, report: dict) -> Optional[str]:
        """Extract the verified name from bureau KYC data."""
        try:
            return report["kyc"]["name_on_pan"]
        except (KeyError, TypeError):
            return None

    def extract_bureau_dob(self, report: dict) -> Optional[str]:
        """Extract the verified DOB from bureau KYC data."""
        try:
            return str(report["kyc"]["dob"])
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize a name for comparison: lowercase, strip, collapse spaces."""
        import re
        return re.sub(r"\s+", " ", name.strip().lower())

    def names_match(self, declared_name: str, bureau_name: str) -> tuple[bool, float]:
        """
        Compare declared name against bureau-verified name.
        Returns (match: bool, confidence: float 0-1).
        Uses token-based similarity to handle order differences
        (e.g. "Sharma Rahul" vs "Rahul Sharma").
        """
        d = self._normalize_name(declared_name)
        b = self._normalize_name(bureau_name)

        # Exact match
        if d == b:
            return True, 1.0

        # Token-based: all tokens of declared must appear in bureau name
        d_tokens = set(d.split())
        b_tokens = set(b.split())

        if not d_tokens or not b_tokens:
            return False, 0.0

        # How many declared tokens are in bureau name?
        common = d_tokens & b_tokens
        if len(common) == 0:
            return False, 0.0

        # Both first and last name must be present
        coverage = len(common) / max(len(d_tokens), len(b_tokens))

        # Accept if at least 80% token overlap (handles middle names, initials)
        return coverage >= 0.8, round(coverage, 2)

    def dob_matches(self, declared_dob: str, bureau_dob: str) -> bool:
        """
        Compare declared DOB against bureau-verified DOB.
        Handles common formats: DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD.
        """
        from datetime import datetime

        def _parse_dob(dob_str: str):
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(dob_str.strip(), fmt).date()
                except ValueError:
                    continue
            return None

        d = _parse_dob(declared_dob)
        b = _parse_dob(bureau_dob)
        if d is None or b is None:
            return False
        return d == b

    async def verify_identity(
        self,
        declared_name: Optional[str],
        declared_dob: Optional[str],
        pan: Optional[str] = None,
    ) -> dict:
        """
        Full identity verification against bureau data.
        Fetches bureau report and cross-checks name and DOB.

        Returns dict with:
          - verified: bool
          - name_match: bool
          - name_confidence: float
          - dob_match: bool
          - bureau_name: str or None
          - bureau_dob: str or None
          - issues: list of issue strings
        """
        result = {
            "verified": False,
            "name_match": False,
            "name_confidence": 0.0,
            "dob_match": False,
            "bureau_name": None,
            "bureau_dob": None,
            "issues": [],
        }

        # Fetch bureau report
        report = await self.fetch_report(pan=pan, name=declared_name)
        if not report:
            result["issues"].append("bureau_unreachable")
            return result

        bureau_name = self.extract_bureau_name(report)
        bureau_dob = self.extract_bureau_dob(report)
        result["bureau_name"] = bureau_name
        result["bureau_dob"] = bureau_dob

        # Name verification
        if declared_name and bureau_name:
            match, confidence = self.names_match(declared_name, bureau_name)
            result["name_match"] = match
            result["name_confidence"] = confidence
            if not match:
                result["issues"].append(
                    f"name_mismatch:declared='{declared_name}' vs bureau='{bureau_name}'"
                )
        elif not declared_name:
            result["issues"].append("name_missing")
        else:
            result["issues"].append("bureau_name_missing")

        # DOB verification
        if declared_dob and bureau_dob:
            result["dob_match"] = self.dob_matches(declared_dob, bureau_dob)
            if not result["dob_match"]:
                result["issues"].append(
                    f"dob_mismatch:declared='{declared_dob}' vs bureau='{bureau_dob}'"
                )
        elif not declared_dob:
            result["issues"].append("dob_missing")

        # Verified if both name and DOB match
        result["verified"] = result["name_match"] and result["dob_match"]
        return result


# Global instance
bureau_client = BureauClient()
