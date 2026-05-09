"""
Shared State – typed, versioned session data object.
All agents read from and write to this via Redis.

Refactor notes:
  - Removed LIVENESS_CHALLENGE stage (merged into OVD_DOCUMENT_CAPTURE)
  - Added doc_authenticity_passed + doc_authenticity_score to CustomerIdentity
  - Stage order: GREETING_CONSENT → OVD_DOCUMENT_CAPTURE → AADHAAR_VERIFICATION
                 → IDENTITY_KYC → EMPLOYMENT_INCOME → LOAN_PURPOSE
                 → RISK_ASSESSMENT → OFFER_ACCEPTANCE → COMPLETED
"""
from __future__ import annotations

import json
import time
from enum import Enum
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict


class SessionStage(str, Enum):
    INIT                  = "INIT"
    GREETING_CONSENT      = "GREETING_CONSENT"
    OVD_DOCUMENT_CAPTURE  = "OVD_DOCUMENT_CAPTURE"   # V-CIP: doc upload + authenticity
    IDENTITY_KYC          = "IDENTITY_KYC"            # Cross-verify name/DOB vs bureau
    EMPLOYMENT_INCOME     = "EMPLOYMENT_INCOME"
    LOAN_PURPOSE          = "LOAN_PURPOSE"
    RISK_ASSESSMENT       = "RISK_ASSESSMENT"
    OFFER_ACCEPTANCE      = "OFFER_ACCEPTANCE"
    COMPLETED             = "COMPLETED"
    ESCALATED             = "ESCALATED"
    ABANDONED             = "ABANDONED"


# Ordered list for stage progression (excludes terminal stages)
STAGE_SEQUENCE = [
    SessionStage.GREETING_CONSENT,
    SessionStage.OVD_DOCUMENT_CAPTURE,
    SessionStage.IDENTITY_KYC,
    SessionStage.EMPLOYMENT_INCOME,
    SessionStage.LOAN_PURPOSE,
    SessionStage.RISK_ASSESSMENT,
    SessionStage.OFFER_ACCEPTANCE,
    SessionStage.COMPLETED,
]


class RiskBand(str, Enum):
    LOW     = "LOW"
    MEDIUM  = "MEDIUM"
    HIGH    = "HIGH"
    UNKNOWN = "UNKNOWN"


@dataclass
class SessionMeta:
    call_id: str
    session_token: str
    created_at: float = field(default_factory=time.time)
    device_fingerprint: Optional[str] = None
    ip_address: Optional[str] = None
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None
    rbi_session_id: Optional[str] = None
    # ── VideoSDK integration ──────────────────────────────────────────────
    videosdk_room_id: Optional[str] = None
    videosdk_participant_id: Optional[str] = None
    videosdk_recording_id: Optional[str] = None
    videosdk_token: Optional[str] = None
    network_quality_score: int = 5   # 1-5 from VideoSDK quality API
    # ── Greeting tracking ───────────────────────────────────────────────────
    greeting_sent: bool = False
    greeting_acknowledged: bool = False


@dataclass
class CustomerIdentity:
    name: Optional[str] = None
    declared_dob: Optional[str] = None
    estimated_age_vision: Optional[int] = None
    aadhaar_masked: Optional[str] = None
    pan_masked: Optional[str] = None

    # Consent
    consent_given: bool = False
    consent_phrase: Optional[str] = None
    consent_timestamp: Optional[float] = None

    # Identity verification
    identity_verified: bool = False
    bureau_verified_name: Optional[str] = None
    face_match_passed: bool = False
    liveness_passed: bool = False            # kept for risk_agent compat

    # OVD Document fields
    ovd_type: Optional[str] = None           # "aadhaar" | "pan" | "passport"
    ovd_number_masked: Optional[str] = None
    ovd_photo_match_score: Optional[float] = None

    # ── Document Authenticity (replaces liveness challenge) ───────────────
    doc_authenticity_passed: bool = False
    doc_authenticity_score: float = 0.0
    doc_authenticity_checks: List[str] = field(default_factory=list)


    # Liveness (kept for API compat – set True when doc auth passes)
    liveness_score: Optional[float] = None
    liveness_challenge_passed: bool = False
    liveness_challenge_type: Optional[str] = None


@dataclass
class FinancialData:
    employment_type: Optional[str] = None
    employer_name: Optional[str] = None
    monthly_income: Optional[float] = None
    income_confidence: float = 0.0
    bureau_score: Optional[int] = None
    propensity_score: Optional[float] = None
    risk_band: RiskBand = RiskBand.UNKNOWN
    bureau_fetched_at: Optional[float] = None
    bureau_report_raw: Optional[Dict[str, Any]] = None
    verified_income: Optional[float] = None
    existing_emi_total: Optional[float] = None
    foir: Optional[float] = None
    credit_utilization: Optional[float] = None
    delinquency_count: int = 0
    hard_inquiries_6m: int = 0
    employment_stability_years: Optional[float] = None
    fraud_flags: List[str] = field(default_factory=list)
    composite_risk_score: Optional[float] = None
    income_verification_source: Optional[str] = None
    geo_distance_km: Optional[float] = None


@dataclass
class ConversationEntry:
    stage: str
    utterance: str
    stt_transcript: str
    stt_confidence: float
    timestamp: float = field(default_factory=time.time)
    agent: Optional[str] = None


@dataclass
class ExtractedSignals:
    loan_purpose: Optional[str] = None
    loan_purpose_category: Optional[str] = None
    loan_amount_requested: Optional[float] = None
    tenure_preference_months: Optional[int] = None


@dataclass
class ModeratorLogEntry:
    stage: str
    agent_activated: str
    action_taken: str
    confidence: float
    escalated_to_human: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class LoanOffer:
    eligible_amount: Optional[float] = None
    tenure_options: List[int] = field(default_factory=list)
    interest_rate: Optional[float] = None
    emi_12m: Optional[float] = None
    emi_24m: Optional[float] = None
    emi_36m: Optional[float] = None
    kfs_url: Optional[str] = None
    offer_explanation: Optional[str] = None
    acceptance_status: Optional[str] = None
    accepted_tenure: Optional[int] = None
    upi_ref: Optional[str] = None


@dataclass
class SharedState:
    """Single source of truth for the entire loan session."""
    session_meta: SessionMeta
    current_stage: SessionStage = SessionStage.INIT
    stage_retry_count: int = 0
    max_retries_per_stage: int = 2

    customer_identity: CustomerIdentity = field(default_factory=CustomerIdentity)
    financial_data: FinancialData = field(default_factory=FinancialData)
    extracted_signals: ExtractedSignals = field(default_factory=ExtractedSignals)
    final_offer: LoanOffer = field(default_factory=LoanOffer)

    conversation_log: List[ConversationEntry] = field(default_factory=list)
    moderator_log: List[ModeratorLogEntry] = field(default_factory=list)

    version: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str) -> "SharedState":
        data = json.loads(raw)
        meta = SessionMeta(**data["session_meta"])
        cid_data = data["customer_identity"]
        cid  = CustomerIdentity(**cid_data)
        fin  = FinancialData(**{
            **data["financial_data"],
            "risk_band": RiskBand(data["financial_data"].get("risk_band", "UNKNOWN"))
        })
        sig  = ExtractedSignals(**data["extracted_signals"])
        off  = LoanOffer(**data["final_offer"])
        conv = [ConversationEntry(**e) for e in data["conversation_log"]]
        mod  = [ModeratorLogEntry(**e) for e in data["moderator_log"]]
        return cls(
            session_meta=meta,
            current_stage=SessionStage(data["current_stage"]),
            stage_retry_count=data["stage_retry_count"],
            max_retries_per_stage=data["max_retries_per_stage"],
            customer_identity=cid,
            financial_data=fin,
            extracted_signals=sig,
            final_offer=off,
            conversation_log=conv,
            moderator_log=mod,
            version=data["version"],
        )

    def redis_key(self) -> str:
        return f"session:{self.session_meta.call_id}:state"

    def next_stage(self) -> Optional[SessionStage]:
        """Return the next stage in the sequence, or None if at end."""
        try:
            idx = STAGE_SEQUENCE.index(self.current_stage)
            if idx + 1 < len(STAGE_SEQUENCE):
                return STAGE_SEQUENCE[idx + 1]
        except ValueError:
            pass
        return None