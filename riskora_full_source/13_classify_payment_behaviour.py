"""SLT Phase 1 — independent customer payment-behaviour classification.

Input:  data/SLT_Payment_Behaviour_Dummy_Data.xlsx
Output: outputs/customer_behaviour_classification.csv
        outputs/behaviour_classification_validation.txt

This is a RULE-BASED prototype using *illustrative* thresholds from the workbook.
It is not a trained creditworthiness/default-prediction model or SLT-approved policy.

Classification uses billing settlement dates and account history. It does NOT
use the workbook's agent_classification or designed_profile labels as inputs.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "SLT_Payment_Behaviour_Dummy_Data.xlsx"
OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)
CSV_OUTPUT = OUT / "customer_behaviour_classification.csv"
VALIDATION_OUTPUT = OUT / "behaviour_classification_validation.txt"


def require_columns(frame: pd.DataFrame, fields: list[str], sheet: str) -> None:
    missing = sorted(set(fields) - set(frame.columns))
    if missing:
        raise ValueError(f"{sheet} sheet is missing columns: {missing}")


def read_thresholds(book: pd.ExcelFile) -> tuple[pd.Timestamp, dict[str, float]]:
    rows = pd.read_excel(book, sheet_name="Thresholds")
    require_columns(rows, ["Parameter", "Value"], "Thresholds")
    lookup = rows.dropna(subset=["Parameter"]).set_index("Parameter")["Value"]
    required = {
        "regular_delay_max": "Regular: max average delay (days)",
        "regular_late_ratio_max": "Regular: max late-payment ratio",
        "consistency_band": "Predictable: consistency band (± days)",
        "consistency_min": "Predictable: min consistency",
        "predictable_delay_max": "Predictable: max average delay (days)",
        "high_risk_overdue_days": "High-risk: current overdue days ≥",
        "high_risk_unpaid_bills": "High-risk: unpaid past-due bills ≥",
    }
    missing = [label for label in required.values() if label not in lookup.index]
    if missing:
        raise ValueError(f"Missing threshold rows: {missing}")
    snapshot = pd.to_datetime(lookup.loc["Snapshot / analysis date"], errors="coerce")
    if pd.isna(snapshot):
        raise ValueError("Missing or invalid snapshot / analysis date")
    limits = {key: float(lookup.loc[label]) for key, label in required.items()}
    return snapshot, limits


def excel_round(value: np.ndarray | float, digits: int = 0):
    """Excel-style ROUND (half away from zero), for matching billing helper."""
    a = np.asarray(value, dtype=float)
    scale = 10**digits
    rounded = np.sign(a) * np.floor(np.abs(a) * scale + 0.5) / scale
    return rounded


def classify_account(row: dict, thresholds: dict[str, float]) -> tuple[str, str]:
    overdue = int(row["current_overdue_days"])
    unpaid = int(row["unpaid_past_due_bills"])
    settled = int(row["settled_bills"])
    avg = row["avg_payment_delay_days"]
    ratio = row["late_ratio"]
    consistency = row["payment_consistency"]

    if overdue >= thresholds["high_risk_overdue_days"] or unpaid >= thresholds["high_risk_unpaid_bills"]:
        label = "High-risk non-payer"
        reason = f"{unpaid} unpaid past-due bill(s); oldest overdue {overdue} days"
    elif settled == 0:
        label = "High-risk non-payer"
        reason = "No settled bill history available; requires manual review"
    elif avg <= thresholds["regular_delay_max"] and ratio <= thresholds["regular_late_ratio_max"]:
        label = "Regular payer"
        reason = f"Average settlement delay {avg:.1f} days; late ratio {ratio:.0%}"
    elif consistency >= thresholds["consistency_min"] and avg <= thresholds["predictable_delay_max"]:
        label = "Predictable late payer"
        reason = f"Consistent payment timing ({consistency:.0%} within ±{thresholds['consistency_band']:g} days); average delay {avg:.1f} days"
    else:
        label = "Irregular payer"
        reason = f"Variable payment pattern: consistency {consistency:.0%}, average delay {avg:.1f} days, late ratio {ratio:.0%}"
    if float(row["current_outstanding_LKR"]) > 0:
        reason += f"; current outstanding LKR {float(row['current_outstanding_LKR']):,.2f}"
    return label, reason


def main() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(
            f"Cannot find {SOURCE}. Place the original Excel workbook inside your data folder."
        )

    with pd.ExcelFile(SOURCE) as book:
        snapshot, limits = read_thresholds(book)
        master = pd.read_excel(book, sheet_name="Customer_Master")
        billing = pd.read_excel(book, sheet_name="Billing")
        collections = pd.read_excel(book, sheet_name="Collection_History")
        suspensions = pd.read_excel(book, sheet_name="Suspension_History")
        # Reference results are used only for validation, not classification.
        reference = pd.read_excel(book, sheet_name="Behaviour_Summary")

    require_columns(master, ["account_no", "customer_id", "customer_type"], "Customer_Master")
    require_columns(
        billing,
        ["account_no", "bill_dtm", "payment_due_dtm", "bill_settled_dtm", "balance_out_amt"],
        "Billing",
    )
    require_columns(collections, ["account_no"], "Collection_History")
    require_columns(suspensions, ["account_no"], "Suspension_History")
    require_columns(reference, ["account_no", "agent_classification"], "Behaviour_Summary")

    if master["account_no"].isna().any() or master["account_no"].duplicated().any():
        raise ValueError("Customer_Master must have unique nonmissing account numbers")
    if billing["account_no"].isna().any():
        raise ValueError("Billing contains missing account numbers")
    unknown_accounts = set(billing["account_no"]) - set(master["account_no"])
    if unknown_accounts:
        raise ValueError(f"Billing contains unknown account numbers: {list(unknown_accounts)[:5]}")

    for field in ["bill_dtm", "payment_due_dtm", "bill_settled_dtm"]:
        billing[field] = pd.to_datetime(billing[field], errors="coerce")
    if billing[["bill_dtm", "payment_due_dtm"]].isna().any().any():
        raise ValueError("Billing contains missing/invalid bill or due dates")
    billing["balance_out_amt"] = pd.to_numeric(billing["balance_out_amt"], errors="raise")
    if (billing["balance_out_amt"] < -0.01).any():
        raise ValueError("Negative balance_out_amt detected; review source data")
    if (billing["bill_dtm"] > snapshot).any():
        raise ValueError("Found billing records dated after the analysis snapshot")

    # Billing's bill_settled_dtm is the completion date of FIFO payments.
    # Calculate delay afresh from the underlying dates; do not use cached
    # settle_delay_days or any pre-filled behaviour classification.
    is_settled = billing["bill_settled_dtm"].notna() & (billing["bill_settled_dtm"] <= snapshot)
    if (billing.loc[is_settled, "bill_settled_dtm"] < billing.loc[is_settled, "bill_dtm"]).any():
        raise ValueError("A bill was marked settled before its billing date")

    delays = (billing["bill_settled_dtm"] - billing["payment_due_dtm"]).dt.total_seconds() / 86400.0
    billing["derived_settle_delay"] = np.where(is_settled, excel_round(delays, 0), np.nan)
    billing["is_past_due_unpaid"] = (~is_settled) & (billing["payment_due_dtm"] < snapshot)
    collections_count = collections.groupby("account_no").size().to_dict()
    suspensions_count = suspensions.groupby("account_no").size().to_dict()

    result_rows: list[dict] = []
    for _, customer in master.iterrows():
        acc = customer["account_no"]
        items = billing.loc[billing["account_no"] == acc].sort_values("bill_dtm")
        if items.empty:
            raise ValueError(f"No billing records for account {acc}")
        paid_delays = items["derived_settle_delay"].dropna().to_numpy(dtype=float)
        paid_count = len(paid_delays)
        unpaid = items.loc[items["is_past_due_unpaid"]]
        unpaid_count = len(unpaid)
        avg = float(excel_round(paid_delays.mean(), 1)) if paid_count else float("nan")
        late_count = int((paid_delays > 0).sum())
        late_ratio = float(late_count / paid_count) if paid_count else float("nan")
        consistency = (
            float((np.abs(paid_delays - avg) <= limits["consistency_band"]).mean())
            if paid_count else float("nan")
        )
        oldest_overdue = (
            int(excel_round((snapshot - unpaid["payment_due_dtm"].min()).total_seconds() / 86400.0))
            if unpaid_count else 0
        )
        latest_bill_date = items["bill_dtm"].max()
        balance = float(items.loc[items["bill_dtm"] == latest_bill_date, "balance_out_amt"].sum())
        record = {
            "account_no": acc,
            "customer_id": customer["customer_id"],
            "customer_type": customer["customer_type"],
            "total_bills": len(items),
            "settled_bills": paid_count,
            "avg_payment_delay_days": avg,
            "late_payments": late_count,
            "late_ratio": late_ratio,
            "payment_consistency": consistency,
            "unpaid_past_due_bills": unpaid_count,
            "current_outstanding_LKR": round(balance, 2),
            "current_overdue_days": oldest_overdue,
            "suspensions": int(suspensions_count.get(acc, 0)),
            "collection_actions": int(collections_count.get(acc, 0)),
        }
        record["behaviour_classification"], record["classification_reason"] = classify_account(record, limits)
        result_rows.append(record)

    results = pd.DataFrame(result_rows).sort_values("account_no")
    assert results["account_no"].is_unique
    assert len(results) == len(master)
    results.to_csv(CSV_OUTPUT, index=False, float_format="%.6f")

    review = results.merge(
        reference[["account_no", "agent_classification", "designed_profile (ground truth)"]],
        on="account_no", how="left", validate="one_to_one"
    )
    mismatch = review[review["behaviour_classification"] != review["agent_classification"]]
    # Dataset designer's intent is an independent reference. The overdue
    # predictable-late profile is still the same four-class behaviour group.
    designed = review["designed_profile (ground truth)"].replace({
        "Predictable late payer (current overdue)": "Predictable late payer",
        "Predictable late payer (currently overdue)": "Predictable late payer",
    })
    ground_truth_mismatch = review[review["behaviour_classification"] != designed]
    # Neither reference label is used to choose a classification.
    current_overdue = (
        results["unpaid_past_due_bills"].gt(0)
        & results["current_overdue_days"].gt(0)
        & results["current_outstanding_LKR"].gt(0)
    )
    lines = [
        "SLT PAYMENT BEHAVIOUR CLASSIFICATION — VALIDATION",
        f"Analysis date: {snapshot:%Y-%m-%d}",
        "Data source: SLT_Payment_Behaviour_Dummy_Data.xlsx (200-account synthetic workbook)",
        "Method: independent rule-based classification from Billing settlement dates",
        "Threshold source: workbook Thresholds sheet (illustrative; NOT approved SLT policy)",
        "Pre-filled classifications and ground-truth labels NOT used as classifier inputs.",
        "",
        f"Accounts assessed: {len(results)}",
        "Predicted class counts:",
        results["behaviour_classification"].value_counts().to_string(),
        "",
        f"Agreement with workbook agent_classification: {len(review) - len(mismatch)}/{len(review)}",
        f"Mismatched accounts vs workbook classification formula: {len(mismatch)}",
        "",
        f"Agreement with designed ground-truth profiles (4 categories): {len(review) - len(ground_truth_mismatch)}/{len(review)}",
        f"Differences vs designed ground truth: {len(ground_truth_mismatch)}",
        "",
        f"Current overdue accounts (same filter as step 12): {int(current_overdue.sum())}",
        f"Current outstanding of overdue accounts (LKR): {results.loc[current_overdue, 'current_outstanding_LKR'].sum():,.2f}",
    ]
    if not mismatch.empty:
        lines.extend([
            "",
            "First 15 classification differences (independent result vs workbook formula):",
            mismatch[["account_no", "behaviour_classification", "agent_classification"]].head(15).to_string(index=False),
        ])
    if not ground_truth_mismatch.empty:
        lines.extend([
            "",
            "Different from designed ground-truth profile (review business thresholds):",
            ground_truth_mismatch[["account_no", "behaviour_classification", "designed_profile (ground truth)"]].to_string(index=False),
        ])
    lines.extend([
        "",
        "Important: 200/200 agreement with workbook agent_classification means the",
        "independent implementation reproduces the SAME threshold rules; it is",
        "not predictive-model accuracy or validation on new/real customer data.",
        "",
        "Operational output: outputs/customer_behaviour_classification.csv",
        "The operational output intentionally excludes ground-truth and workbook reference labels.",
        "Note: Payment behaviour classification is distinct from immediate collection action.",
        "Collection recommendations and creditworthiness scoring are separate upcoming stages.",
    ])
    VALIDATION_OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("CUSTOMER PAYMENT BEHAVIOUR CLASSIFICATION")
    print("Snapshot:", f"{snapshot:%Y-%m-%d}")
    print("Accounts:", len(results))
    print(results["behaviour_classification"].value_counts().to_string())
    print(f"Agreement with workbook formula: {len(review) - len(mismatch)}/{len(review)}")
    print(f"Agreement with designed profiles: {len(review) - len(ground_truth_mismatch)}/{len(review)}")
    print(f"Currently overdue accounts: {int(current_overdue.sum())}")
    print("Saved:", CSV_OUTPUT)
    print("Validation report:", VALIDATION_OUTPUT)
    print("IMPORTANT: These are draft rule-based categories, not SLT-approved decisions.")


if __name__ == "__main__":
    main()
