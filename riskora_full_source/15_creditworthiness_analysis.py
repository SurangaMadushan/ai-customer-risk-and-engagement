"""SLT Phase 1: payment-based reliability (creditworthiness proxy) prototype.

Prerequisite: python 13_classify_payment_behaviour.py
Input: data/SLT_Payment_Behaviour_Dummy_Data.xlsx
       outputs/customer_behaviour_classification.csv
Output: outputs/customer_creditworthiness.csv
        outputs/creditworthiness_methodology.txt

IMPORTANT: Illustrative analyst-designed weights, NOT an SLT-approved credit
score, not a credit bureau rating, and not a prediction of future default.
Do not use these scores for automated adverse customer decisions.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
WORKBOOK = ROOT / "data" / "SLT_Payment_Behaviour_Dummy_Data.xlsx"
CLASSIFICATION = ROOT / "outputs" / "customer_behaviour_classification.csv"
OUT = ROOT / "outputs"
OUTFILE = OUT / "customer_creditworthiness.csv"
METHOD = OUT / "creditworthiness_methodology.txt"


def account_ids(s: pd.Series) -> pd.Series:
    # String IDs avoid losing leading zeros and prevent numeric/string merge mismatch.
    return s.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def check_columns(frame: pd.DataFrame, required: list[str], source: str) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise ValueError(f"Missing columns in {source}: {missing}")


def make_scores() -> pd.DataFrame:
    if not WORKBOOK.exists():
        raise FileNotFoundError(f"Workbook not found: {WORKBOOK}")
    if not CLASSIFICATION.exists():
        raise FileNotFoundError(
            "Classification output not found. Run: python 13_classify_payment_behaviour.py"
        )

    profiles = pd.read_csv(CLASSIFICATION, dtype={"account_no": "string", "customer_id": "string"})
    required = [
        "account_no", "customer_id", "total_bills", "settled_bills",
        "avg_payment_delay_days", "payment_consistency", "unpaid_past_due_bills",
        "current_outstanding_LKR", "current_overdue_days", "collection_actions",
        "suspensions", "behaviour_classification",
    ]
    check_columns(profiles, required, CLASSIFICATION.name)
    if profiles["account_no"].isna().any() or profiles["account_no"].duplicated().any():
        raise ValueError("Account numbers in classification must be unique and present")

    # Billing sheet is used only for invoice-level exposure normalisation.
    # No workbook 'agent_classification' or 'designed_profile' labels are used.
    bills = pd.read_excel(
        WORKBOOK, sheet_name="Billing",
        usecols=["account_no", "invoice_net_amt", "invoice_tax_amt"],
        engine="openpyxl",
    )
    bills["account_no"] = account_ids(bills["account_no"])
    for field in ["invoice_net_amt", "invoice_tax_amt"]:
        bills[field] = pd.to_numeric(bills[field], errors="raise")
    bills["bill_total_lkr"] = (bills["invoice_net_amt"] + bills["invoice_tax_amt"]).clip(lower=0)
    means = (
        bills.groupby("account_no", dropna=False)["bill_total_lkr"]
        .mean().rename("average_invoice_LKR").reset_index()
    )
    profiles["account_no"] = account_ids(profiles["account_no"])
    result = profiles.merge(means, on="account_no", how="left", validate="one_to_one")
    if result["average_invoice_LKR"].isna().any():
        raise ValueError("Could not match some customer accounts to the Billing sheet")

    numeric = [
        "total_bills", "settled_bills", "avg_payment_delay_days",
        "payment_consistency", "unpaid_past_due_bills", "current_outstanding_LKR",
        "current_overdue_days", "collection_actions", "suspensions",
    ]
    for field in numeric:
        result[field] = pd.to_numeric(result[field], errors="coerce")
    required_complete = [c for c in numeric if c not in ("avg_payment_delay_days", "payment_consistency")]
    if result[required_complete].isna().any().any():
        raise ValueError("Required numeric customer features contain missing/invalid values")
    if (result["total_bills"] <= 0).any() or (result["settled_bills"] > result["total_bills"]).any():
        raise ValueError("Invalid total or settled bill counts")
    if (result[["current_outstanding_LKR", "current_overdue_days", "unpaid_past_due_bills"]] < 0).any().any():
        raise ValueError("Negative outstanding, overdue days or unpaid past-due bills")

    bills_settled = result["settled_bills"]
    # Profiles with fewer than 3 completed bills are insufficient to judge
    # a payment pattern; their score and band are left blank, not labelled bad.
    sufficient = bills_settled >= 3
    paid_share = (bills_settled / result["total_bills"]).clip(0, 1)
    consistency = result["payment_consistency"].clip(0, 1).fillna(0)
    delay_days = result["avg_payment_delay_days"].clip(lower=0).fillna(30)

    # Illustrative points total 100. These are NOT statistical probabilities.
    result["points_settlement"] = (25 * paid_share).round(2)
    result["points_consistency"] = (20 * consistency).round(2)
    result["points_delay"] = (15 * (1 - (delay_days / 30).clip(0, 1))).round(2)
    arrears_severity = (
        0.6 * (result["current_overdue_days"] / 90).clip(0, 1)
        + 0.4 * (result["unpaid_past_due_bills"] / 3).clip(0, 1)
    ).clip(0, 1)
    result["points_current_arrears"] = (25 * (1 - arrears_severity)).round(2)

    avg_bill = result["average_invoice_LKR"].clip(lower=0)
    balance = result["current_outstanding_LKR"]
    exposure = pd.Series(
        np.where(avg_bill > 0, balance / avg_bill.replace(0, np.nan), np.where(balance > 0, 3.0, 0.0)),
        index=result.index,
    ).fillna(3.0)
    result["outstanding_to_average_invoice"] = exposure.round(3)
    result["points_balance_exposure"] = (10 * (1 - (exposure / 3).clip(0, 1))).round(2)
    collection_severity = (
        0.5 * (result["collection_actions"] / 12).clip(0, 1)
        + 0.5 * (result["suspensions"] / 2).clip(0, 1)
    ).clip(0, 1)
    result["points_collection_history"] = (5 * (1 - collection_severity)).round(2)

    parts = [
        "points_settlement", "points_consistency", "points_delay",
        "points_current_arrears", "points_balance_exposure", "points_collection_history",
    ]
    result["payment_reliability_score"] = result[parts].sum(axis=1).round(1)
    result.loc[~sufficient, "payment_reliability_score"] = np.nan
    result["reliability_band"] = "Needs manual review: insufficient settled history"
    score = result["payment_reliability_score"]
    result.loc[sufficient & (score >= 80), "reliability_band"] = "Strong payment reliability"
    result.loc[sufficient & (score >= 60) & (score < 80), "reliability_band"] = "Moderate payment reliability"
    result.loc[sufficient & (score >= 40) & (score < 60), "reliability_band"] = "Low payment reliability"
    result.loc[sufficient & (score < 40), "reliability_band"] = "Very low payment reliability"

    def reason(r: pd.Series) -> str:
        if pd.isna(r["payment_reliability_score"]):
            return f"Only {int(r['settled_bills'])} settled bills; manual review required"
        messages: list[str] = []
        if r["unpaid_past_due_bills"] > 0:
            messages.append(f"{int(r['unpaid_past_due_bills'])} unpaid past-due bill(s), {int(r['current_overdue_days'])} days overdue")
        else:
            messages.append("No currently overdue unpaid bills")
        messages.append(f"{int(r['settled_bills'])} of {int(r['total_bills'])} bills settled")
        if r["payment_consistency"] >= 0.8:
            messages.append("stable payment timing")
        elif r["payment_consistency"] < 0.5:
            messages.append("variable payment timing")
        if r["suspensions"] > 0:
            messages.append(f"{int(r['suspensions'])} past suspension(s)")
        messages.append(f"LKR {r['current_outstanding_LKR']:,.2f} current balance")
        return "; ".join(messages)

    result["score_reason"] = result.apply(reason, axis=1)
    result["score_status"] = np.where(sufficient, "PROTOTYPE ONLY — not SLT approved", "Manual review")
    # Exclude any source workbook designer labels. Do not use demographics in scoring.
    first = [
        "account_no", "customer_id", "customer_type", "behaviour_classification",
        "payment_reliability_score", "reliability_band", "score_status",
        "score_reason", "average_invoice_LKR", "outstanding_to_average_invoice",
    ]
    tail = [c for c in result.columns if c not in first]
    result = result[first + tail].sort_values("account_no")
    if len(result) != len(profiles) or result["account_no"].duplicated().any():
        raise AssertionError("Expected exactly one output row per account")

    OUT.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTFILE, index=False, float_format="%.3f")
    METHOD.write_text(
        "SLT — Payment Reliability / Creditworthiness Proxy (PROTOTYPE)\n"
        "==============================================================\n"
        "Source: Step 13 classification features + invoice totals from Billing\n"
        "Not a bank/credit-bureau score or future-default probability.\n"
        "Not authorized for autonomous customer decisions.\n\n"
        "Scoring (illustrative weights, out of 100):\n"
        "  25 points: settled bills / total bills\n"
        "  20 points: payment timing consistency\n"
        "  15 points: positive average payment delay, capped at 30 days\n"
        "  25 points: current overdue days (90-day cap) and unpaid past-due bills (3-bill cap)\n"
        "  10 points: outstanding balance relative to average monthly invoice (3x cap)\n"
        "   5 points: collection actions (12 cap) and suspensions (2 cap)\n\n"
        "Proposed interpretation: 80-100 Strong; 60-79.9 Moderate; 40-59.9 Low; below 40 Very low.\n"
        "Fewer than 3 settled bills: no score; manual review.\n"
        "All weights, cutoffs, definitions and time horizons require SLT business approval.\n"
        "The input workbook contains synthetic records; do not infer real creditworthiness.\n"
        "Source reference class labels and customer type are NOT scoring features.\n"
        "This result should complement, not replace, customer behaviour and human review.\n",
        encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    data = make_scores()
    print("SLT PAYMENT RELIABILITY / CREDITWORTHINESS PROXY")
    print("Accounts assessed:", len(data))
    print(data["reliability_band"].value_counts(dropna=False).to_string())
    print("Accounts currently overdue:", int(((data["unpaid_past_due_bills"] > 0) & (data["current_overdue_days"] > 0)).sum()))
    print("Saved:", OUTFILE)
    print("Methodology:", METHOD)
    print("NOTE: Illustrative internal prototype; not SLT-approved creditworthiness or ML prediction.")
