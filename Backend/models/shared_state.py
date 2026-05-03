"""
Shared State – typed, versioned session data object.
All agents read from and write to this via Redis.
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
    OVD_DOCUMENT_CAPTURE  = "OVD_DOCUMENT_CAPTURE"      # V-CIP: show Aadhaar/PAN on camera
    LIVENESS_CHALLENGE    = "LIVENESS_CHALLENGE"          # V-CIP: blink/head turn/read OTP
    AADHAAR_VERIFICATION  = "AADHAAR_VERIFICATION"        # V-CIP: OTP-based e-KYC
    IDENTITY_KYC          = "IDENTITY_KYC"                # Cross-verify name/DOB against bureau
    EMPLOYMENT_INCOME     = "EMPLOYMENT_INCOME"
    LOAN_PURPOSE          = "LOAN_PURPOSE"
    RISK_ASSESSMENT       = "RISK_ASSESSMENT"
    OFFER_ACCEPTANCE      = "OFFER_ACCEPTANCE"
    COMPLETED             = "COMPLETED"
    ESCALATED             = "ESCALATED"
    ABANDONED             = "ABANDONED"


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
    videosdk_room_id: Optional[str] = None          # VideoSDK meeting room
    videosdk_participant_id: Optional[str] = None   # Customer participant ID
    videosdk_recording_id: Optional[str] = None     # Auto-recording ID from VideoSDK
    videosdk_token: Optional[str] = None            # Short-lived JWT for this session
    network_quality_score: int = 5                   # 1-5 from VideoSDK quality API


@dataclass
class CustomerIdentity:
    name: Optional[str] = None
    declared_dob: Optional[str] = None
    estimated_age_vision: Optional[int] = None
    aadhaar_masked: Optional[str] = None
    pan_masked: Optional[str] = None
    liveness_score: Optional[float] = None
    liveness_passed: bool = False
    consent_given: bool = False
    consent_phrase: Optional[str] = None
    consent_timestamp: Optional[float] = None
    # ── V-CIP and bureau verification fields ─────────────────────────────
    identity_verified: bool = False               # True if name+DOB match bureau
    bureau_verified_name: Optional[str] = None    # Name from bureau KYC data
    ovd_type: Optional[str] = None                # "aadhaar" | "pan" | "passport"
    ovd_number_masked: Optional[str] = None       # Masked OVD number shown on camera
    ovd_photo_match_score: Optional[float] = None # Face match score vs OVD photo
    liveness_challenge_passed: bool = False        # V-CIP liveness challenge result
    liveness_challenge_type: Optional[str] = None  # "blink" | "head_turn" | "read_otp"
    aadhaar_otp_verified: bool = False             # OTP-based Aadhaar verification


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
    # ── New: enriched from bureau report ──────────────────────────────────────
    bureau_report_raw: Optional[Dict[str, Any]] = None  # Full JSON for audit trail
    verified_income: Optional[float] = None              # Bureau-verified monthly income
    existing_emi_total: Optional[float] = None           # Sum of all existing EMIs
    foir: Optional[float] = None                         # Fixed Obligation to Income Ratio
    credit_utilization: Optional[float] = None           # % of credit limit used
    delinquency_count: int = 0                           # DPD 30+ events in last 12m
    hard_inquiries_6m: int = 0                           # Recent credit shopping count
    employment_stability_years: Optional[float] = None   # Years at current employment
    fraud_flags: List[str] = field(default_factory=list) # Active fraud alerts
    composite_risk_score: Optional[float] = None         # 0-100 composite score
    income_verification_source: Optional[str] = None     # bureau_verified / unavailable / pending_docs
    geo_distance_km: Optional[float] = None              # Device/IP vs declared geo distance


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
    loan_purpose_category: Optional[str] = None   # home/education/business/personal
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
class WorkerDeltaEvent:
    """Supervisor-owned state patch contract emitted by workers."""
    call_id: str
    agent: str
    expected_version: int
    idempotency_key: str
    delta: Dict[str, Any]
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
    acceptance_status: Optional[str] = None   # ACCEPTED / DECLINED / PENDING
    accepted_tenure: Optional[int] = None
    upi_ref: Optional[str] = None


@dataclass
class SharedState:
    """Single source of truth for the entire loan session."""
    # ── Core ──────────────────────────────────────────────────────────────────
    session_meta: SessionMeta
    current_stage: SessionStage = SessionStage.INIT
    stage_retry_count: int = 0
    max_retries_per_stage: int = 2

    # ── Entities ──────────────────────────────────────────────────────────────
    customer_identity: CustomerIdentity = field(default_factory=CustomerIdentity)
    financial_data: FinancialData = field(default_factory=FinancialData)
    extracted_signals: ExtractedSignals = field(default_factory=ExtractedSignals)
    final_offer: LoanOffer = field(default_factory=LoanOffer)

    # ── Logs ──────────────────────────────────────────────────────────────────
    conversation_log: List[ConversationEntry] = field(default_factory=list)
    moderator_log: List[ModeratorLogEntry] = field(default_factory=list)

    # ── Version for optimistic locking ───────────────────────────────────────
    version: int = 0

    # ── Serialisation ─────────────────────────────────────────────────────────
    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str) -> "SharedState":
        data = json.loads(raw)
        meta = SessionMeta(**data["session_meta"])
        cid  = CustomerIdentity(**data["customer_identity"])
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