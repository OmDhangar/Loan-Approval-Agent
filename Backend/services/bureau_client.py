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
        """Extract verified (or declared) monthly income."""
        try:
            ip = report["income_profile"]
            return ip.get("verified_monthly") or ip.get("declared_monthly")
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


# Global instance
bureau_client = BureauClient()
