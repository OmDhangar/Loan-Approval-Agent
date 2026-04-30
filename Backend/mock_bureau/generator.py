"""
Mock Bureau – Random Profile Generator
───────────────────────────────────────
Generates realistic randomised bureau reports for unknown PANs.
Produces natural variability across all risk dimensions.
"""

import random
import uuid
from datetime import date, datetime, timedelta

# Indian lender names for realistic tradelines
LENDERS = [
    "HDFC Bank", "ICICI Bank", "SBI", "Axis Bank", "Kotak Mahindra",
    "Bajaj Finance", "Tata Capital", "IndusInd Bank", "Yes Bank",
    "IDFC First", "RBL Bank", "Federal Bank", "Bank of Baroda",
    "PNB", "Canara Bank", "Fullerton India", "Muthoot Finance",
]

EMPLOYER_NAMES = [
    "TCS", "Infosys", "Wipro", "HCL Technologies", "Tech Mahindra",
    "Reliance Industries", "Tata Motors", "L&T", "Bajaj Auto",
    "Godrej Industries", "Asian Paints", "Dr. Reddy's", "Sun Pharma",
    "State Govt", "Central Govt", "Self-Employed", "Zomato", "Swiggy",
]

INDIAN_CITIES = [
    ("Mumbai", "MH", "400001"), ("Delhi", "DL", "110001"),
    ("Bangalore", "KA", "560001"), ("Hyderabad", "TG", "500001"),
    ("Chennai", "TN", "600001"), ("Pune", "MH", "411001"),
    ("Kolkata", "WB", "700001"), ("Ahmedabad", "GJ", "380001"),
    ("Jaipur", "RJ", "302001"), ("Lucknow", "UP", "226001"),
    ("Nagpur", "MH", "440001"), ("Indore", "MP", "452001"),
]

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh",
    "Ishita", "Ananya", "Diya", "Myra", "Sara", "Aanya", "Aadhya",
    "Rohan", "Karan", "Nikhil", "Sneha", "Pooja", "Meera", "Ravi",
]

LAST_NAMES = [
    "Sharma", "Patel", "Singh", "Kumar", "Reddy", "Nair", "Joshi",
    "Deshmukh", "Iyer", "Gupta", "Verma", "Mishra", "Rao", "Pillai",
    "Kulkarni", "Patil", "Shah", "Mehta", "Bhat", "Menon",
]


def _random_date(start_year: int, end_year: int) -> str:
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    delta = (end - start).days
    return (start + timedelta(days=random.randint(0, delta))).isoformat()


def _generate_pan() -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return (
        "".join(random.choices(letters, k=5))
        + "".join(random.choices("0123456789", k=4))
        + random.choice(letters)
    )


def generate_random_profile(pan: str = None) -> dict:
    """Generate a complete randomised bureau report."""

    pan = pan or _generate_pan()
    name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    city, state, pin = random.choice(INDIAN_CITIES)
    dob = _random_date(1980, 2002)

    # Randomly pick a risk tier and generate accordingly
    tier = random.choices(["low", "medium", "high"], weights=[40, 35, 25])[0]

    if tier == "low":
        cibil = random.randint(730, 860)
        income = random.randint(50000, 200000)
        emp_type = random.choice(["SALARIED", "SALARIED", "BUSINESS_OWNER"])
        emp_years = round(random.uniform(3.0, 15.0), 1)
        util_ratio = round(random.uniform(0.05, 0.30), 2)
        dpd30 = 0
        dpd60 = 0
        written_off = False
        bounces = 0
        hard_inq_6m = random.randint(0, 1)
        salary_mode = "BANK_TRANSFER"
        risk_band = "LOW"
    elif tier == "medium":
        cibil = random.randint(650, 729)
        income = random.randint(25000, 65000)
        emp_type = random.choice(["SALARIED", "SELF_EMPLOYED", "FREELANCER"])
        emp_years = round(random.uniform(1.0, 5.0), 1)
        util_ratio = round(random.uniform(0.30, 0.65), 2)
        dpd30 = random.randint(0, 2)
        dpd60 = 0
        written_off = False
        bounces = random.randint(0, 2)
        hard_inq_6m = random.randint(1, 3)
        salary_mode = random.choice(["BANK_TRANSFER", "MIXED"])
        risk_band = "MEDIUM"
    else:
        cibil = random.randint(400, 649)
        income = random.randint(12000, 35000)
        emp_type = random.choice(["FREELANCER", "SELF_EMPLOYED", "UNEMPLOYED"])
        emp_years = round(random.uniform(0.2, 2.5), 1)
        util_ratio = round(random.uniform(0.60, 0.95), 2)
        dpd30 = random.randint(2, 5)
        dpd60 = random.randint(1, 3)
        written_off = random.choice([True, False])
        bounces = random.randint(2, 6)
        hard_inq_6m = random.randint(3, 7)
        salary_mode = random.choice(["CASH", "MIXED", "UPI"])
        risk_band = "HIGH"

    # Derived values
    credit_limit = random.randint(50000, 800000)
    current_balance = int(credit_limit * util_ratio)
    num_accounts = random.randint(1, 6)
    active = random.randint(1, num_accounts)
    closed = num_accounts - active
    secured = random.randint(0, min(2, active))
    unsecured = num_accounts - secured
    oldest_months = random.randint(6, 144)
    existing_emi = random.randint(0, int(income * 0.4))
    foir = round(existing_emi / income, 2) if income > 0 else 0.0

    # Bank insights
    monthly_inflow = income + random.randint(-5000, 15000)
    monthly_outflow = int(monthly_inflow * random.uniform(0.65, 0.95))
    cash_flow = round(monthly_inflow / monthly_outflow, 2) if monthly_outflow > 0 else 1.0
    avg_balance = random.randint(5000, int(income * 2.5))

    verified_income = int(income * random.uniform(0.85, 1.0))

    # Tradelines
    tradelines = []
    account_types = ["CREDIT_CARD", "PERSONAL_LOAN", "AUTO_LOAN", "HOME_LOAN", "TWO_WHEELER_LOAN", "GOLD_LOAN"]
    for i in range(min(num_accounts, 4)):
        at = random.choice(account_types)
        lender = random.choice(LENDERS)
        opened = _random_date(2018, 2025)
        is_active = i < active
        status = "ACTIVE" if is_active else random.choice(["CLOSED", "SETTLED"])
        if written_off and i == 0:
            status = "WRITTEN_OFF"
        tl = {
            "account_type": at,
            "lender": lender,
            "opened_on": opened,
            "status": status,
            "credit_limit": random.randint(30000, 500000) if at == "CREDIT_CARD" else None,
            "sanctioned_amount": random.randint(50000, 500000) if at != "CREDIT_CARD" else None,
            "current_balance": random.randint(0, 200000) if is_active else 0,
            "emi_amount": random.randint(2000, 20000) if at != "CREDIT_CARD" and is_active else None,
            "payment_history": "OK" if dpd30 == 0 else random.choice(["OK", "30+", "60+"]),
            "dpd": 0 if dpd30 == 0 else random.randint(0, 90),
            "last_payment_date": _random_date(2026, 2026) if is_active else None,
        }
        if not is_active:
            tl["closed_on"] = _random_date(2024, 2025)
        tradelines.append(tl)

    # Inquiries
    inquiry_list = []
    for _ in range(hard_inq_6m):
        inquiry_list.append({
            "bureau": random.choice(["CIBIL", "Experian"]),
            "lender": random.choice(LENDERS),
            "inquiry_date": _random_date(2025, 2026),
            "purpose": random.choice(["PERSONAL_LOAN", "CREDIT_CARD", "BUSINESS_LOAN"]),
        })

    employer = random.choice(EMPLOYER_NAMES)
    if emp_type in ("SELF_EMPLOYED", "FREELANCER"):
        employer = f"Self - {name.split()[0]} Enterprise"

    req_id = f"REQ-{uuid.uuid4().hex[:6]}"
    sess_id = f"SES-{uuid.uuid4().hex[:4]}"
    cons_id = f"CONS-{uuid.uuid4().hex[:4]}"
    now = datetime.utcnow().isoformat() + "Z"

    # Score factors
    factors = []
    if cibil >= 750:
        factors.append("Strong credit score")
    elif cibil >= 700:
        factors.append("Adequate credit score")
    else:
        factors.append(f"Below-average credit score ({cibil})")

    if util_ratio < 0.30:
        factors.append("Low credit utilization")
    elif util_ratio > 0.60:
        factors.append(f"High credit utilization ({int(util_ratio*100)}%)")

    if dpd30 == 0:
        factors.append("No recent delinquencies")
    else:
        factors.append(f"{dpd30} delinquency events in last 12 months")

    if emp_years >= 3:
        factors.append(f"Stable employment ({emp_years} years)")
    else:
        factors.append(f"Short employment tenure ({emp_years} years)")

    # Alerts
    alerts = []
    if written_off:
        alerts.append("Written-off account on record")
    if bounces >= 3:
        alerts.append(f"{bounces} NACH bounces in 6 months")
    if hard_inq_6m >= 4:
        alerts.append("Credit-hungry behaviour detected")
    if foir > 0.50:
        alerts.append(f"High FOIR ({int(foir*100)}%)")

    return {
        "request_meta": {
            "request_id": req_id, "timestamp": now, "source": "mock_bureau_sandbox",
            "consent": {"captured": True, "mode": "voice", "consent_id": cons_id, "consent_ts": now}
        },
        "kyc": {
            "pan": pan, "pan_status": "VALID", "name_match": True,
            "name_on_pan": name, "dob": dob,
            "address": {"line1": f"House {random.randint(1,500)}, {city}", "city": city, "state": state, "pin": pin, "country": "IN"}
        },
        "credit_summary": {
            "score": {"bureau": "CIBIL", "value": cibil, "range": "300-900", "risk_band": risk_band},
            "accounts_summary": {
                "total_accounts": num_accounts, "active_accounts": active, "closed_accounts": closed,
                "secured_accounts": secured, "unsecured_accounts": unsecured, "oldest_account_months": oldest_months
            },
            "utilization": {"credit_limit": credit_limit, "current_balance": current_balance, "utilization_ratio": util_ratio},
            "delinquency": {"dpd_30_plus_last_12m": dpd30, "dpd_60_plus_last_12m": dpd60, "dpd_90_plus_ever": 0, "written_off": written_off, "max_dpd_last_24m": dpd60 * 30 + dpd30 * 15}
        },
        "tradelines": tradelines,
        "income_profile": {
            "declared_monthly": income, "verified_monthly": verified_income,
            "employer_name": employer, "employer_type": "MNC" if emp_type == "SALARIED" else "SME",
            "employment_type": emp_type, "employment_stability_years": emp_years,
            "salary_mode": salary_mode
        },
        "bank_insights": {
            "avg_balance_6m": avg_balance, "monthly_inflow": monthly_inflow, "monthly_outflow": monthly_outflow,
            "cash_flow_ratio": cash_flow, "bounce_count_6m": bounces, "salary_credits_regularity": round(random.uniform(0.3, 1.0), 1) if emp_type != "UNEMPLOYED" else 0.0
        },
        "emi_obligations": {"existing_emi_total": existing_emi, "existing_loan_count": active, "foir": foir},
        "credit_inquiries": {
            "hard_inquiries_last_6m": hard_inq_6m,
            "hard_inquiries_last_12m": hard_inq_6m + random.randint(0, 3),
            "last_inquiry_date": inquiry_list[0]["inquiry_date"] if inquiry_list else None,
            "inquiry_list": inquiry_list
        },
        "fraud_flags": {
            "identity_fraud_alert": False,
            "synthetic_id_risk": random.random() < 0.02,
            "device_velocity_alert": hard_inq_6m >= 5,
            "pan_aadhaar_mismatch": False,
        },
        "alternate_data": {
            "telecom_score": cibil + random.randint(-80, 50),
            "upi_tx_count_6m": random.randint(20, 350),
            "gst_filing_status": random.choice(["REGULAR", "IRREGULAR", "NOT_REGISTERED", None]),
            "epfo_member": emp_type == "SALARIED",
        },
        "score_factors": factors,
        "alerts": alerts,
        "audit": {"session_id": sess_id, "ip_geo": f"IN-{state}", "device_fp": f"fp_{uuid.uuid4().hex[:4]}", "generated_by": "mock_engine_v2"}
    }
