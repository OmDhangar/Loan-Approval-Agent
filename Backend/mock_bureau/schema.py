"""
Mock Bureau – Pydantic Response Schema
───────────────────────────────────────
Production-grade schema modelling CIBIL / Experian bureau report responses.
Every field is documented with its downstream impact on risk decisioning.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class PANStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    DEACTIVATED = "DEACTIVATED"


class RiskBandLabel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class EmploymentType(str, Enum):
    SALARIED = "SALARIED"
    SELF_EMPLOYED = "SELF_EMPLOYED"
    FREELANCER = "FREELANCER"
    BUSINESS_OWNER = "BUSINESS_OWNER"
    RETIRED = "RETIRED"
    UNEMPLOYED = "UNEMPLOYED"


class AccountType(str, Enum):
    CREDIT_CARD = "CREDIT_CARD"
    PERSONAL_LOAN = "PERSONAL_LOAN"
    HOME_LOAN = "HOME_LOAN"
    AUTO_LOAN = "AUTO_LOAN"
    EDUCATION_LOAN = "EDUCATION_LOAN"
    GOLD_LOAN = "GOLD_LOAN"
    BUSINESS_LOAN = "BUSINESS_LOAN"
    TWO_WHEELER_LOAN = "TWO_WHEELER_LOAN"
    OVERDRAFT = "OVERDRAFT"


class AccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    WRITTEN_OFF = "WRITTEN_OFF"
    SETTLED = "SETTLED"


class SalaryMode(str, Enum):
    BANK_TRANSFER = "BANK_TRANSFER"
    CASH = "CASH"
    MIXED = "MIXED"
    UPI = "UPI"


# ── Sub-models ────────────────────────────────────────────────────────────────

class Consent(BaseModel):
    captured: bool = True
    mode: str = "voice"
    consent_id: str
    consent_ts: datetime


class RequestMeta(BaseModel):
    request_id: str
    timestamp: datetime
    source: str = "mock_bureau_sandbox"
    consent: Consent


class Address(BaseModel):
    line1: Optional[str] = None
    city: str
    state: str          # 2-letter state code (MH, DL, KA, etc.)
    pin: Optional[str] = None
    country: str = "IN"


class KYC(BaseModel):
    """PAN verification & basic identity.  Impact: name/DOB mismatch = fraud flag."""
    pan: str
    pan_status: PANStatus
    name_match: bool
    name_on_pan: str
    dob: date
    address: Address


class CreditScore(BaseModel):
    """CIBIL/Experian score.  Impact: primary risk signal (30% of composite)."""
    bureau: str = "CIBIL"
    value: int = Field(ge=300, le=900)
    range: str = "300-900"
    risk_band: RiskBandLabel


class AccountsSummary(BaseModel):
    """Credit account mix.  Impact: thin file (< 2 accounts) = conservative scoring."""
    total_accounts: int
    active_accounts: int
    closed_accounts: int
    secured_accounts: int       # home/auto/gold loans
    unsecured_accounts: int     # credit cards, personal loans
    oldest_account_months: int  # credit history depth


class Utilization(BaseModel):
    """Credit utilisation ratio.  Impact: > 60% utilisation = risk flag (+0.5% rate)."""
    credit_limit: float
    current_balance: float
    utilization_ratio: float = Field(ge=0.0, le=1.0)


class Delinquency(BaseModel):
    """Payment defaults.  Impact: any DPD-60+ = auto-MEDIUM; write-off = auto-HIGH."""
    dpd_30_plus_last_12m: int
    dpd_60_plus_last_12m: int
    dpd_90_plus_ever: int = 0
    written_off: bool
    max_dpd_last_24m: int = 0


class CreditSummary(BaseModel):
    score: CreditScore
    accounts_summary: AccountsSummary
    utilization: Utilization
    delinquency: Delinquency


class Tradeline(BaseModel):
    """Individual credit account.  Impact: payment history pattern feeds composite score."""
    account_type: AccountType
    lender: str
    opened_on: date
    closed_on: Optional[date] = None
    status: AccountStatus
    credit_limit: Optional[float] = None
    sanctioned_amount: Optional[float] = None
    current_balance: float
    emi_amount: Optional[float] = None
    payment_history: str          # e.g. "OK", "30+", "60+", "WRITTEN_OFF"
    dpd: int = 0
    last_payment_date: Optional[date] = None


class IncomeProfile(BaseModel):
    """Income & employment.  Impact: stability < 2yr = +0.5% rate; cash salary = risk flag."""
    declared_monthly: float
    verified_monthly: Optional[float] = None      # Bank-statement verified
    employer_name: Optional[str] = None
    employer_type: Optional[str] = None            # MNC / Govt / Startup / SME
    employment_type: EmploymentType
    employment_stability_years: float
    salary_mode: SalaryMode


class BankInsights(BaseModel):
    """Bank transaction analysis.  Impact: low avg balance = cashflow risk; bounces = major red flag."""
    avg_balance_6m: float
    monthly_inflow: float
    monthly_outflow: float
    cash_flow_ratio: float       # inflow / outflow — healthy > 1.2
    bounce_count_6m: int         # cheque/NACH bounces
    salary_credits_regularity: float = Field(ge=0.0, le=1.0)  # 1.0 = every month on time


class EMIObligation(BaseModel):
    """Existing debt burden.  Impact: FOIR > 0.5 = stressed; > 0.65 = reject."""
    existing_emi_total: float
    existing_loan_count: int
    foir: float = Field(ge=0.0, le=1.0)    # Fixed Obligation to Income Ratio


class CreditInquiry(BaseModel):
    bureau: str
    lender: str
    inquiry_date: date
    purpose: str         # "PERSONAL_LOAN", "CREDIT_CARD", etc.


class CreditInquiries(BaseModel):
    """Recent credit shopping.  Impact: > 3 inquiries in 6m = credit-hungry flag."""
    hard_inquiries_last_6m: int
    hard_inquiries_last_12m: int
    last_inquiry_date: Optional[date] = None
    inquiry_list: List[CreditInquiry] = []


class FraudFlags(BaseModel):
    """Fraud / identity risk signals.  Impact: any True = auto-escalate to human review."""
    identity_fraud_alert: bool = False
    synthetic_id_risk: bool = False
    device_velocity_alert: bool = False
    pan_aadhaar_mismatch: bool = False


class AlternateData(BaseModel):
    """Optional enrichment from non-bureau sources.  Impact: positive signals can offset thin file."""
    telecom_score: Optional[int] = None          # 300-900 range
    upi_tx_count_6m: Optional[int] = None        # UPI transaction volume
    gst_filing_status: Optional[str] = None      # "REGULAR" / "IRREGULAR" / "NOT_REGISTERED"
    epfo_member: Optional[bool] = None           # Employee PF membership


class Audit(BaseModel):
    session_id: str
    ip_geo: str
    device_fp: str
    generated_by: str = "mock_engine_v2"


# ── Top-level Response ────────────────────────────────────────────────────────

class BureauReport(BaseModel):
    """
    Complete credit bureau report.
    This is the single response object returned by the mock bureau API.
    """
    request_meta: RequestMeta
    kyc: KYC
    credit_summary: CreditSummary
    tradelines: List[Tradeline]
    income_profile: IncomeProfile
    bank_insights: BankInsights
    emi_obligations: EMIObligation
    credit_inquiries: CreditInquiries
    fraud_flags: FraudFlags
    alternate_data: AlternateData
    score_factors: List[str]
    alerts: List[str]
    audit: Audit
