"""
Mock Bureau – API Router
────────────────────────
Embedded in the main FastAPI app.
Simulates a credit bureau API endpoint for development/testing.

Endpoints:
  GET  /report?pan=XXXXX      → Return bureau report by PAN
  GET  /report?name=XXXXX     → Return bureau report by name
  GET  /personas               → List available test personas
  GET  /health                 → Health check
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from mock_bureau.personas import PERSONA_BY_PAN, PERSONA_BY_NAME
from mock_bureau.generator import generate_random_profile

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/report")
async def get_bureau_report(
    pan: Optional[str] = Query(None, description="PAN number (e.g. BWDPS1234K)"),
    name: Optional[str] = Query(None, description="Customer name (partial match)"),
    user_id: Optional[str] = Query(None, description="Alias for PAN"),
):
    """
    Fetch a credit bureau report.

    - Known PANs return hardcoded personas (deterministic, reproducible).
    - Unknown PANs return a randomly generated profile (different each call).
    - Name search does case-insensitive partial match against known personas.

    Test PANs:
      BWDPS1234K → Low Risk  (Rahul Sharma)
      CXRPM5678L → Medium Risk (Priya Deshmukh)
      DZNFA9012M → High Risk (Vikram Patil)
      EKMPK4567N → Thin File (Ananya Kulkarni)
      FRTPJ7890Q → High Income Poor Behaviour (Sameer Joshi)
    """
    lookup_pan = pan or user_id

    # 1. Try PAN lookup
    if lookup_pan:
        lookup_pan = lookup_pan.upper().strip()
        if lookup_pan in PERSONA_BY_PAN:
            logger.info(f"Bureau report: known persona for PAN {lookup_pan}")
            return PERSONA_BY_PAN[lookup_pan]
        else:
            logger.info(f"Bureau report: generating random profile for PAN {lookup_pan}")
            return generate_random_profile(pan=lookup_pan)

    # 2. Try name lookup
    if name:
        name_lower = name.strip().lower()
        for persona_name, persona in PERSONA_BY_NAME.items():
            if name_lower in persona_name or persona_name in name_lower:
                logger.info(f"Bureau report: matched persona by name '{name}'")
                return persona
        # No match — generate random with a random PAN
        logger.info(f"Bureau report: no name match for '{name}', generating random")
        return generate_random_profile()

    raise HTTPException(
        status_code=400,
        detail="Provide 'pan', 'user_id', or 'name' query parameter. "
               "Test PANs: BWDPS1234K (low), CXRPM5678L (medium), DZNFA9012M (high), "
               "EKMPK4567N (thin file), FRTPJ7890Q (high income poor behaviour)"
    )


@router.get("/personas")
async def list_personas():
    """List all available test personas with their PANs and risk levels."""
    return {
        "personas": [
            {"pan": "BWDPS1234K", "name": "Rahul Sharma",    "risk": "LOW",    "income": 95000,  "cibil": 782, "profile": "Stable salaried IT professional"},
            {"pan": "CXRPM5678L", "name": "Priya Deshmukh",  "risk": "MEDIUM", "income": 42000,  "cibil": 688, "profile": "Self-employed boutique owner"},
            {"pan": "DZNFA9012M", "name": "Vikram Patil",    "risk": "HIGH",   "income": 22000,  "cibil": 548, "profile": "Freelance gig worker, poor credit"},
            {"pan": "EKMPK4567N", "name": "Ananya Kulkarni", "risk": "MEDIUM", "income": 35000,  "cibil": -1,  "profile": "Fresh graduate, no credit history"},
            {"pan": "FRTPJ7890Q", "name": "Sameer Joshi",    "risk": "HIGH",   "income": 180000, "cibil": 635, "profile": "High income, reckless credit behaviour"},
        ],
        "usage": "GET /report?pan=BWDPS1234K  or  GET /report?name=Rahul",
        "note": "Unknown PANs will generate a random profile"
    }


@router.get("/health")
async def bureau_health():
    return {"status": "ok", "service": "mock-credit-bureau", "version": "2.0"}
