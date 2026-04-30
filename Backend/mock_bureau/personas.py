"""
Mock Bureau – Hardcoded Persona Profiles
────────────────────────────────────────
5 realistic Indian customer profiles for testing the full loan workflow.
Query by PAN to get a specific persona.
"""

from datetime import date, datetime

# ── Persona 1: LOW RISK ──────────────────────────────────────────────────────
# Rahul Sharma — Stable salaried IT professional in Pune
LOW_RISK = {
    "request_meta": {
        "request_id": "REQ-lr001",
        "timestamp": "2026-04-30T10:22:11Z",
        "source": "mock_bureau_sandbox",
        "consent": {
            "captured": True, "mode": "voice",
            "consent_id": "CONS-lr01", "consent_ts": "2026-04-30T10:21:55Z"
        }
    },
    "kyc": {
        "pan": "BWDPS1234K", "pan_status": "VALID", "name_match": True,
        "name_on_pan": "Rahul Sharma", "dob": "1994-05-10",
        "address": {"line1": "Flat 402, Amanora Park", "city": "Pune", "state": "MH", "pin": "411028", "country": "IN"}
    },
    "credit_summary": {
        "score": {"bureau": "CIBIL", "value": 782, "range": "300-900", "risk_band": "LOW"},
        "accounts_summary": {
            "total_accounts": 5, "active_accounts": 3, "closed_accounts": 2,
            "secured_accounts": 1, "unsecured_accounts": 4, "oldest_account_months": 96
        },
        "utilization": {"credit_limit": 500000, "current_balance": 85000, "utilization_ratio": 0.17},
        "delinquency": {"dpd_30_plus_last_12m": 0, "dpd_60_plus_last_12m": 0, "dpd_90_plus_ever": 0, "written_off": False, "max_dpd_last_24m": 0}
    },
    "tradelines": [
        {"account_type": "CREDIT_CARD", "lender": "HDFC Bank", "opened_on": "2018-04-15", "status": "ACTIVE", "credit_limit": 300000, "current_balance": 45000, "emi_amount": None, "payment_history": "OK", "dpd": 0, "last_payment_date": "2026-04-05"},
        {"account_type": "CREDIT_CARD", "lender": "ICICI Bank", "opened_on": "2020-08-01", "status": "ACTIVE", "credit_limit": 200000, "current_balance": 40000, "emi_amount": None, "payment_history": "OK", "dpd": 0, "last_payment_date": "2026-04-10"},
        {"account_type": "AUTO_LOAN", "lender": "Bajaj Finance", "opened_on": "2022-01-20", "status": "ACTIVE", "sanctioned_amount": 600000, "current_balance": 180000, "credit_limit": None, "emi_amount": 12500, "payment_history": "OK", "dpd": 0, "last_payment_date": "2026-04-01"},
        {"account_type": "PERSONAL_LOAN", "lender": "SBI", "opened_on": "2019-06-10", "closed_on": "2022-06-10", "status": "CLOSED", "sanctioned_amount": 200000, "current_balance": 0, "credit_limit": None, "emi_amount": None, "payment_history": "OK", "dpd": 0},
        {"account_type": "EDUCATION_LOAN", "lender": "Bank of Baroda", "opened_on": "2016-07-01", "closed_on": "2021-07-01", "status": "CLOSED", "sanctioned_amount": 400000, "current_balance": 0, "credit_limit": None, "emi_amount": None, "payment_history": "OK", "dpd": 0}
    ],
    "income_profile": {
        "declared_monthly": 95000, "verified_monthly": 92000,
        "employer_name": "Infosys Ltd", "employer_type": "MNC",
        "employment_type": "SALARIED", "employment_stability_years": 6.5,
        "salary_mode": "BANK_TRANSFER"
    },
    "bank_insights": {
        "avg_balance_6m": 185000, "monthly_inflow": 105000, "monthly_outflow": 72000,
        "cash_flow_ratio": 1.46, "bounce_count_6m": 0, "salary_credits_regularity": 1.0
    },
    "emi_obligations": {"existing_emi_total": 12500, "existing_loan_count": 1, "foir": 0.13},
    "credit_inquiries": {
        "hard_inquiries_last_6m": 0, "hard_inquiries_last_12m": 1,
        "last_inquiry_date": "2025-11-15", "inquiry_list": [
            {"bureau": "CIBIL", "lender": "Amazon Pay", "inquiry_date": "2025-11-15", "purpose": "CREDIT_CARD"}
        ]
    },
    "fraud_flags": {"identity_fraud_alert": False, "synthetic_id_risk": False, "device_velocity_alert": False, "pan_aadhaar_mismatch": False},
    "alternate_data": {"telecom_score": 810, "upi_tx_count_6m": 245, "gst_filing_status": None, "epfo_member": True},
    "score_factors": ["Excellent payment history across 8 years", "Low credit utilization (17%)", "No delinquencies ever", "Stable salaried employment (6.5 years)", "Strong cash flow ratio"],
    "alerts": [],
    "audit": {"session_id": "SES-lr01", "ip_geo": "IN-MH", "device_fp": "fp_lr01", "generated_by": "mock_engine_v2"}
}


# ── Persona 2: MEDIUM RISK ───────────────────────────────────────────────────
# Priya Deshmukh — Self-employed boutique owner in Nagpur
MEDIUM_RISK = {
    "request_meta": {
        "request_id": "REQ-mr001",
        "timestamp": "2026-04-30T10:25:00Z",
        "source": "mock_bureau_sandbox",
        "consent": {
            "captured": True, "mode": "voice",
            "consent_id": "CONS-mr01", "consent_ts": "2026-04-30T10:24:45Z"
        }
    },
    "kyc": {
        "pan": "CXRPM5678L", "pan_status": "VALID", "name_match": True,
        "name_on_pan": "Priya Deshmukh", "dob": "1990-11-22",
        "address": {"line1": "Shop 12, Dharampeth", "city": "Nagpur", "state": "MH", "pin": "440010", "country": "IN"}
    },
    "credit_summary": {
        "score": {"bureau": "CIBIL", "value": 688, "range": "300-900", "risk_band": "MEDIUM"},
        "accounts_summary": {
            "total_accounts": 3, "active_accounts": 2, "closed_accounts": 1,
            "secured_accounts": 0, "unsecured_accounts": 3, "oldest_account_months": 42
        },
        "utilization": {"credit_limit": 200000, "current_balance": 108000, "utilization_ratio": 0.54},
        "delinquency": {"dpd_30_plus_last_12m": 1, "dpd_60_plus_last_12m": 0, "dpd_90_plus_ever": 0, "written_off": False, "max_dpd_last_24m": 32}
    },
    "tradelines": [
        {"account_type": "CREDIT_CARD", "lender": "Axis Bank", "opened_on": "2022-10-05", "status": "ACTIVE", "credit_limit": 100000, "current_balance": 68000, "emi_amount": None, "payment_history": "OK", "dpd": 0, "last_payment_date": "2026-03-28"},
        {"account_type": "PERSONAL_LOAN", "lender": "Bajaj Finserv", "opened_on": "2024-01-15", "status": "ACTIVE", "sanctioned_amount": 150000, "current_balance": 95000, "credit_limit": None, "emi_amount": 8500, "payment_history": "30+", "dpd": 0, "last_payment_date": "2026-04-02"},
        {"account_type": "CREDIT_CARD", "lender": "Kotak Mahindra", "opened_on": "2023-03-20", "closed_on": "2025-06-01", "status": "CLOSED", "credit_limit": 100000, "current_balance": 0, "emi_amount": None, "payment_history": "OK", "dpd": 0}
    ],
    "income_profile": {
        "declared_monthly": 42000, "verified_monthly": 38000,
        "employer_name": "Self - Priya Boutique", "employer_type": "SME",
        "employment_type": "SELF_EMPLOYED", "employment_stability_years": 3.5,
        "salary_mode": "MIXED"
    },
    "bank_insights": {
        "avg_balance_6m": 45000, "monthly_inflow": 55000, "monthly_outflow": 48000,
        "cash_flow_ratio": 1.15, "bounce_count_6m": 1, "salary_credits_regularity": 0.7
    },
    "emi_obligations": {"existing_emi_total": 8500, "existing_loan_count": 1, "foir": 0.20},
    "credit_inquiries": {
        "hard_inquiries_last_6m": 2, "hard_inquiries_last_12m": 3,
        "last_inquiry_date": "2026-02-20", "inquiry_list": [
            {"bureau": "CIBIL", "lender": "HDFC Bank", "inquiry_date": "2026-02-20", "purpose": "PERSONAL_LOAN"},
            {"bureau": "CIBIL", "lender": "Tata Capital", "inquiry_date": "2026-01-10", "purpose": "BUSINESS_LOAN"},
            {"bureau": "Experian", "lender": "IDFC First", "inquiry_date": "2025-09-05", "purpose": "CREDIT_CARD"}
        ]
    },
    "fraud_flags": {"identity_fraud_alert": False, "synthetic_id_risk": False, "device_velocity_alert": False, "pan_aadhaar_mismatch": False},
    "alternate_data": {"telecom_score": 680, "upi_tx_count_6m": 120, "gst_filing_status": "IRREGULAR", "epfo_member": False},
    "score_factors": ["Moderate credit utilization (54%)", "One DPD-30 in last 12 months", "Thin credit file (3.5 years)", "Self-employed income variability", "Multiple recent credit inquiries"],
    "alerts": ["NACH bounce detected in last 6 months", "Irregular GST filing pattern"],
    "audit": {"session_id": "SES-mr01", "ip_geo": "IN-MH", "device_fp": "fp_mr01", "generated_by": "mock_engine_v2"}
}


# ── Persona 3: HIGH RISK ─────────────────────────────────────────────────────
# Vikram Patil — Freelance delivery driver in Mumbai, poor credit history
HIGH_RISK = {
    "request_meta": {
        "request_id": "REQ-hr001",
        "timestamp": "2026-04-30T10:30:00Z",
        "source": "mock_bureau_sandbox",
        "consent": {
            "captured": True, "mode": "voice",
            "consent_id": "CONS-hr01", "consent_ts": "2026-04-30T10:29:40Z"
        }
    },
    "kyc": {
        "pan": "DZNFA9012M", "pan_status": "VALID", "name_match": True,
        "name_on_pan": "Vikram Patil", "dob": "1996-08-15",
        "address": {"line1": "Room 3, Kurla East", "city": "Mumbai", "state": "MH", "pin": "400070", "country": "IN"}
    },
    "credit_summary": {
        "score": {"bureau": "CIBIL", "value": 548, "range": "300-900", "risk_band": "HIGH"},
        "accounts_summary": {
            "total_accounts": 4, "active_accounts": 2, "closed_accounts": 2,
            "secured_accounts": 1, "unsecured_accounts": 3, "oldest_account_months": 30
        },
        "utilization": {"credit_limit": 120000, "current_balance": 105000, "utilization_ratio": 0.88},
        "delinquency": {"dpd_30_plus_last_12m": 3, "dpd_60_plus_last_12m": 2, "dpd_90_plus_ever": 1, "written_off": True, "max_dpd_last_24m": 120}
    },
    "tradelines": [
        {"account_type": "CREDIT_CARD", "lender": "IndusInd Bank", "opened_on": "2023-10-01", "status": "ACTIVE", "credit_limit": 50000, "current_balance": 48000, "emi_amount": None, "payment_history": "60+", "dpd": 65, "last_payment_date": "2026-01-15"},
        {"account_type": "TWO_WHEELER_LOAN", "lender": "Hero FinCorp", "opened_on": "2024-02-10", "status": "ACTIVE", "sanctioned_amount": 85000, "current_balance": 57000, "credit_limit": None, "emi_amount": 3200, "payment_history": "30+", "dpd": 35, "last_payment_date": "2026-02-28"},
        {"account_type": "PERSONAL_LOAN", "lender": "MoneyTap", "opened_on": "2023-05-01", "closed_on": "2025-08-01", "status": "WRITTEN_OFF", "sanctioned_amount": 70000, "current_balance": 42000, "credit_limit": None, "emi_amount": None, "payment_history": "WRITTEN_OFF", "dpd": 0},
        {"account_type": "CREDIT_CARD", "lender": "RBL Bank", "opened_on": "2024-06-15", "closed_on": "2025-12-01", "status": "SETTLED", "credit_limit": 70000, "current_balance": 0, "emi_amount": None, "payment_history": "90+", "dpd": 0}
    ],
    "income_profile": {
        "declared_monthly": 22000, "verified_monthly": 18000,
        "employer_name": "Self - Gig Worker", "employer_type": "Gig",
        "employment_type": "FREELANCER", "employment_stability_years": 1.2,
        "salary_mode": "CASH"
    },
    "bank_insights": {
        "avg_balance_6m": 8500, "monthly_inflow": 25000, "monthly_outflow": 23500,
        "cash_flow_ratio": 1.06, "bounce_count_6m": 4, "salary_credits_regularity": 0.3
    },
    "emi_obligations": {"existing_emi_total": 3200, "existing_loan_count": 1, "foir": 0.15},
    "credit_inquiries": {
        "hard_inquiries_last_6m": 5, "hard_inquiries_last_12m": 8,
        "last_inquiry_date": "2026-04-10", "inquiry_list": [
            {"bureau": "CIBIL", "lender": "KreditBee", "inquiry_date": "2026-04-10", "purpose": "PERSONAL_LOAN"},
            {"bureau": "CIBIL", "lender": "CASHe", "inquiry_date": "2026-03-22", "purpose": "PERSONAL_LOAN"},
            {"bureau": "CIBIL", "lender": "MoneyTap", "inquiry_date": "2026-02-15", "purpose": "PERSONAL_LOAN"},
            {"bureau": "Experian", "lender": "Navi", "inquiry_date": "2026-01-08", "purpose": "PERSONAL_LOAN"},
            {"bureau": "CIBIL", "lender": "Slice", "inquiry_date": "2025-12-01", "purpose": "CREDIT_CARD"}
        ]
    },
    "fraud_flags": {"identity_fraud_alert": False, "synthetic_id_risk": False, "device_velocity_alert": True, "pan_aadhaar_mismatch": False},
    "alternate_data": {"telecom_score": 520, "upi_tx_count_6m": 85, "gst_filing_status": "NOT_REGISTERED", "epfo_member": False},
    "score_factors": ["Written-off account on record", "Very high credit utilization (88%)", "Multiple delinquencies (DPD 60+ and 90+)", "Excessive credit inquiries (5 in 6 months)", "Cash-based income, low bank balance"],
    "alerts": ["Written-off account: MoneyTap ₹42,000", "Device velocity alert flagged", "4 NACH bounces in 6 months", "Credit-hungry behaviour detected"],
    "audit": {"session_id": "SES-hr01", "ip_geo": "IN-MH", "device_fp": "fp_hr01", "generated_by": "mock_engine_v2"}
}


# ── Persona 4: EDGE CASE – Thin File / New-to-Credit ─────────────────────────
# Ananya Kulkarni — Fresh graduate, first job, no credit history
THIN_FILE = {
    "request_meta": {
        "request_id": "REQ-tf001",
        "timestamp": "2026-04-30T10:35:00Z",
        "source": "mock_bureau_sandbox",
        "consent": {
            "captured": True, "mode": "voice",
            "consent_id": "CONS-tf01", "consent_ts": "2026-04-30T10:34:40Z"
        }
    },
    "kyc": {
        "pan": "EKMPK4567N", "pan_status": "VALID", "name_match": True,
        "name_on_pan": "Ananya Kulkarni", "dob": "2001-03-14",
        "address": {"line1": "B-204, Hinjewadi Phase 2", "city": "Pune", "state": "MH", "pin": "411057", "country": "IN"}
    },
    "credit_summary": {
        "score": {"bureau": "CIBIL", "value": -1, "range": "300-900", "risk_band": "MEDIUM"},
        "accounts_summary": {
            "total_accounts": 0, "active_accounts": 0, "closed_accounts": 0,
            "secured_accounts": 0, "unsecured_accounts": 0, "oldest_account_months": 0
        },
        "utilization": {"credit_limit": 0, "current_balance": 0, "utilization_ratio": 0.0},
        "delinquency": {"dpd_30_plus_last_12m": 0, "dpd_60_plus_last_12m": 0, "dpd_90_plus_ever": 0, "written_off": False, "max_dpd_last_24m": 0}
    },
    "tradelines": [],
    "income_profile": {
        "declared_monthly": 35000, "verified_monthly": 34000,
        "employer_name": "TCS", "employer_type": "MNC",
        "employment_type": "SALARIED", "employment_stability_years": 0.8,
        "salary_mode": "BANK_TRANSFER"
    },
    "bank_insights": {
        "avg_balance_6m": 28000, "monthly_inflow": 38000, "monthly_outflow": 25000,
        "cash_flow_ratio": 1.52, "bounce_count_6m": 0, "salary_credits_regularity": 1.0
    },
    "emi_obligations": {"existing_emi_total": 0, "existing_loan_count": 0, "foir": 0.0},
    "credit_inquiries": {
        "hard_inquiries_last_6m": 0, "hard_inquiries_last_12m": 0,
        "last_inquiry_date": None, "inquiry_list": []
    },
    "fraud_flags": {"identity_fraud_alert": False, "synthetic_id_risk": False, "device_velocity_alert": False, "pan_aadhaar_mismatch": False},
    "alternate_data": {"telecom_score": 720, "upi_tx_count_6m": 190, "gst_filing_status": None, "epfo_member": True},
    "score_factors": ["No credit history (new-to-credit)", "Stable salaried income via bank transfer", "Zero existing obligations", "Good digital transaction footprint"],
    "alerts": ["New-to-credit: no bureau score available"],
    "audit": {"session_id": "SES-tf01", "ip_geo": "IN-MH", "device_fp": "fp_tf01", "generated_by": "mock_engine_v2"}
}


# ── Persona 5: EDGE CASE – High Income but Poor Behaviour ────────────────────
# Sameer Joshi — Senior manager, high salary, reckless credit behaviour
HIGH_INCOME_POOR_BEHAVIOUR = {
    "request_meta": {
        "request_id": "REQ-hp001",
        "timestamp": "2026-04-30T10:40:00Z",
        "source": "mock_bureau_sandbox",
        "consent": {
            "captured": True, "mode": "voice",
            "consent_id": "CONS-hp01", "consent_ts": "2026-04-30T10:39:30Z"
        }
    },
    "kyc": {
        "pan": "FRTPJ7890Q", "pan_status": "VALID", "name_match": True,
        "name_on_pan": "Sameer Joshi", "dob": "1985-12-03",
        "address": {"line1": "1201, Lodha Palava", "city": "Mumbai", "state": "MH", "pin": "421204", "country": "IN"}
    },
    "credit_summary": {
        "score": {"bureau": "CIBIL", "value": 635, "range": "300-900", "risk_band": "HIGH"},
        "accounts_summary": {
            "total_accounts": 8, "active_accounts": 6, "closed_accounts": 2,
            "secured_accounts": 2, "unsecured_accounts": 6, "oldest_account_months": 120
        },
        "utilization": {"credit_limit": 1200000, "current_balance": 840000, "utilization_ratio": 0.70},
        "delinquency": {"dpd_30_plus_last_12m": 2, "dpd_60_plus_last_12m": 1, "dpd_90_plus_ever": 0, "written_off": False, "max_dpd_last_24m": 68}
    },
    "tradelines": [
        {"account_type": "HOME_LOAN", "lender": "SBI", "opened_on": "2020-06-15", "status": "ACTIVE", "sanctioned_amount": 4500000, "current_balance": 3200000, "credit_limit": None, "emi_amount": 38000, "payment_history": "OK", "dpd": 0, "last_payment_date": "2026-04-05"},
        {"account_type": "AUTO_LOAN", "lender": "HDFC Bank", "opened_on": "2023-03-01", "status": "ACTIVE", "sanctioned_amount": 800000, "current_balance": 520000, "credit_limit": None, "emi_amount": 18000, "payment_history": "30+", "dpd": 15, "last_payment_date": "2026-03-20"},
        {"account_type": "CREDIT_CARD", "lender": "Amex", "opened_on": "2017-01-10", "status": "ACTIVE", "credit_limit": 500000, "current_balance": 380000, "emi_amount": None, "payment_history": "60+", "dpd": 0, "last_payment_date": "2026-04-01"},
        {"account_type": "CREDIT_CARD", "lender": "ICICI Bank", "opened_on": "2019-05-20", "status": "ACTIVE", "credit_limit": 400000, "current_balance": 290000, "emi_amount": None, "payment_history": "30+", "dpd": 0, "last_payment_date": "2026-03-25"},
        {"account_type": "CREDIT_CARD", "lender": "Axis Bank", "opened_on": "2021-08-01", "status": "ACTIVE", "credit_limit": 300000, "current_balance": 170000, "emi_amount": None, "payment_history": "OK", "dpd": 0, "last_payment_date": "2026-04-08"},
        {"account_type": "PERSONAL_LOAN", "lender": "Tata Capital", "opened_on": "2025-01-20", "status": "ACTIVE", "sanctioned_amount": 500000, "current_balance": 420000, "credit_limit": None, "emi_amount": 22000, "payment_history": "OK", "dpd": 0, "last_payment_date": "2026-04-01"}
    ],
    "income_profile": {
        "declared_monthly": 180000, "verified_monthly": 175000,
        "employer_name": "Reliance Industries", "employer_type": "MNC",
        "employment_type": "SALARIED", "employment_stability_years": 8.0,
        "salary_mode": "BANK_TRANSFER"
    },
    "bank_insights": {
        "avg_balance_6m": 95000, "monthly_inflow": 195000, "monthly_outflow": 185000,
        "cash_flow_ratio": 1.05, "bounce_count_6m": 2, "salary_credits_regularity": 1.0
    },
    "emi_obligations": {"existing_emi_total": 78000, "existing_loan_count": 3, "foir": 0.43},
    "credit_inquiries": {
        "hard_inquiries_last_6m": 3, "hard_inquiries_last_12m": 5,
        "last_inquiry_date": "2026-03-15", "inquiry_list": [
            {"bureau": "CIBIL", "lender": "Fullerton", "inquiry_date": "2026-03-15", "purpose": "PERSONAL_LOAN"},
            {"bureau": "CIBIL", "lender": "Bajaj Finance", "inquiry_date": "2026-01-25", "purpose": "PERSONAL_LOAN"},
            {"bureau": "Experian", "lender": "HDFC Bank", "inquiry_date": "2025-11-10", "purpose": "CREDIT_CARD"}
        ]
    },
    "fraud_flags": {"identity_fraud_alert": False, "synthetic_id_risk": False, "device_velocity_alert": False, "pan_aadhaar_mismatch": False},
    "alternate_data": {"telecom_score": 750, "upi_tx_count_6m": 310, "gst_filing_status": None, "epfo_member": True},
    "score_factors": ["High credit utilization (70%)", "Multiple DPD events despite high income", "High FOIR (43%) with 3 active loans", "Tight cash flow despite ₹1.8L income", "Credit-hungry behaviour"],
    "alerts": ["FOIR approaching stress threshold (43%)", "2 NACH bounces despite high salary", "Over-leveraged: 6 active credit lines"],
    "audit": {"session_id": "SES-hp01", "ip_geo": "IN-MH", "device_fp": "fp_hp01", "generated_by": "mock_engine_v2"}
}


# ── Lookup table ──────────────────────────────────────────────────────────────

PERSONA_BY_PAN = {
    "BWDPS1234K": LOW_RISK,
    "CXRPM5678L": MEDIUM_RISK,
    "DZNFA9012M": HIGH_RISK,
    "EKMPK4567N": THIN_FILE,
    "FRTPJ7890Q": HIGH_INCOME_POOR_BEHAVIOUR,
}

# Also allow lookup by name (case-insensitive partial match)
PERSONA_BY_NAME = {
    "rahul sharma": LOW_RISK,
    "priya deshmukh": MEDIUM_RISK,
    "vikram patil": HIGH_RISK,
    "ananya kulkarni": THIN_FILE,
    "sameer joshi": HIGH_INCOME_POOR_BEHAVIOUR,
}
