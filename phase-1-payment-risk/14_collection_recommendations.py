"""SLT Phase 1 — explainable collection recommendations (DRAFT rules).

Prerequisite:
    python 13_classify_payment_behaviour.py
Run:
    python 14_collection_recommendations.py

Input:
    outputs/customer_behaviour_classification.csv
    data/SLT_Payment_Behaviour_Dummy_Data.xlsx (Thresholds and validation ONLY)
Outputs:
    outputs/collection_recommendations.csv
    outputs/collection_followup_list.csv
    outputs/collection_recommendations_validation.txt

The workbook's pre-filled recommended_action is NEVER used to make a decision.
These are illustrative rules, not SLT-approved policies. Do not automate calls,
suspensions, collections or other customer actions from this file.
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
CLASSIFIED = ROOT / "outputs" / "customer_behaviour_classification.csv"
WORKBOOK = ROOT / "data" / "SLT_Payment_Behaviour_Dummy_Data.xlsx"
OUTPUT = ROOT / "outputs"
ALL_REPORT = OUTPUT / "collection_recommendations.csv"
FOLLOWUP_REPORT = OUTPUT / "collection_followup_list.csv"
VALIDATION_REPORT = OUTPUT / "collection_recommendations_validation.txt"

REQUIRED_COLUMNS = [
    "account_no", "customer_id", "behaviour_classification",
    "settled_bills", "avg_payment_delay_days", "payment_consistency",
    "unpaid_past_due_bills", "current_outstanding_LKR",
    "current_overdue_days", "collection_actions", "suspensions",
]


def load_business_thresholds() -> tuple[dict[str, float], str]:
    if not WORKBOOK.is_file():
        raise FileNotFoundError(f"Workbook missing: {WORKBOOK}")
    params = pd.read_excel(WORKBOOK, sheet_name="Thresholds", engine="openpyxl")
    if not {"Parameter", "Value"}.issubset(params.columns):
        raise ValueError("Thresholds sheet must have Parameter and Value columns")
    values = params.dropna(subset=["Parameter"]).set_index("Parameter")["Value"]
    labels = {
        "priority_days": "High-risk: current overdue days ≥",
        "priority_bills": "High-risk: unpaid past-due bills ≥",
        "grace_days": "Guardrail: grace over usual delay (days)",
        "max_no_followup_balance": "Guardrail: max outstanding for no follow-up (LKR)",
    }
    missing = [label for label in labels.values() if label not in values.index]
    if missing:
        raise ValueError(f"Missing expected Thresholds rows: {missing}")
    thresholds = {key: float(values.loc[label]) for key, label in labels.items()}
    if any(pd.isna(v) or v < 0 for v in thresholds.values()):
        raise ValueError("Thresholds must be nonnegative numeric values")
    if thresholds["priority_days"] <= 0 or thresholds["priority_bills"] < 1:
        raise ValueError("Priority thresholds must be positive")
    snapshot = pd.to_datetime(values.loc["Snapshot / analysis date"], errors="coerce")
    if pd.isna(snapshot):
        raise ValueError("Missing or invalid analysis date in Thresholds sheet")
    return thresholds, f"{snapshot:%Y-%m-%d}"


def recommend(row: pd.Series, t: dict[str, float]) -> pd.Series:
    group = str(row["behaviour_classification"]).strip()
    overdue_bills = int(row["unpaid_past_due_bills"])
    days = int(row["current_overdue_days"])
    amount = float(row["current_outstanding_LKR"])
    collections = int(row["collection_actions"])
    suspensions = int(row["suspensions"])
    settled = int(row["settled_bills"])
    avg_delay = row["avg_payment_delay_days"]

    has_overdue = overdue_bills > 0 and days > 0 and amount > 0
    historical_pattern = group in {"Regular payer", "Predictable late payer"}
    # A stable payer is NOT exempted from follow-up when arrears exceed guardrails.
    allowed_delay = max(0.0, float(avg_delay)) + t["grace_days"] if pd.notna(avg_delay) else 0.0

    notes: list[str] = [f"Payment behaviour: {group}"]
    if has_overdue:
        notes.append(
            f"{overdue_bills} unpaid past-due bill(s), oldest {days} days overdue, "
            f"LKR {amount:,.2f} outstanding"
        )
    elif amount > 0:
        notes.append(f"LKR {amount:,.2f} outstanding, but no past-due unpaid bill")
    else:
        notes.append("No current unpaid past-due balance")

    if suspensions > 0:
        notes.append(f"{suspensions} previous suspension(s)")
    if collections > 0:
        notes.append(f"{collections} historical collection action(s)")

    # Safety / coverage: an account with insufficient settled history goes to
    # manual review instead of automatic 'no follow-up'.
    if settled < 3:
        action = "Normal follow-up" if has_overdue else "No follow-up"
        notes.append("Insufficient settled payment history; manual review required")
        review = True
        rule = "insufficient_history"
    elif has_overdue and (days >= t["priority_days"] or overdue_bills >= t["priority_bills"]):
        action = "Priority follow-up"
        notes.append("Severe arrears cross the draft overdue-day or unpaid-bill threshold")
        review = True
        rule = "severe_arrears"
    elif has_overdue and (amount > t["max_no_followup_balance"]):
        # Even reliable late payers are not exempt when current debt is high.
        action = "Normal follow-up"
        notes.append("Past-due balance exceeds the draft no-follow-up limit")
        review = True
        rule = "outstanding_guardrail"
    elif has_overdue and historical_pattern and days <= allowed_delay:
        action = "No follow-up"
        notes.append(f"Within usual payment delay plus {t['grace_days']:g}-day grace")
        review = True  # Any overdue account requires business-rule review.
        rule = "predictable_grace"
    elif has_overdue:
        action = "Normal follow-up"
        notes.append("Past-due payment requires routine collection review")
        review = True
        rule = "current_arrears"
    elif group == "Irregular payer":
        action = "Normal follow-up"
        notes.append("Irregular timing: routine monitoring only; no overdue collection call indicated")
        review = False
        rule = "irregular_monitoring"
    elif group == "High-risk non-payer":
        action = "Normal follow-up"
        notes.append("Historical risk indicator only; review status before contacting customer")
        review = True
        rule = "historical_risk_review"
    else:
        action = "No follow-up"
        notes.append("No immediate collection needed under these draft rules")
        review = False
        rule = "no_current_arrears"

    return pd.Series({
        "recommended_action": action,
        "recommendation_reason": "; ".join(notes),
        "recommendation_rule": rule,
        "currently_overdue": bool(has_overdue),
        "requires_human_review": bool(review),
        "recommendation_status": "DRAFT - SLT business approval required",
    })


def main() -> None:
    if not CLASSIFIED.is_file():
        raise FileNotFoundError(
            f"Step 13 output is missing: {CLASSIFIED}\n"
            "First run: python 13_classify_payment_behaviour.py"
        )
    thresholds, snapshot = load_business_thresholds()
    customers = pd.read_csv(CLASSIFIED, dtype={"account_no": "string", "customer_id": "string"})
    missing = sorted(set(REQUIRED_COLUMNS) - set(customers.columns))
    if missing:
        raise ValueError(f"Step 13 output is missing columns: {missing}")
    if customers["account_no"].isna().any() or customers["account_no"].duplicated().any():
        raise ValueError("Missing or duplicate account_no in Step 13 results")
    if customers["customer_id"].isna().any():
        raise ValueError("Missing customer_id in Step 13 results")
    numeric = [
        "settled_bills", "avg_payment_delay_days", "payment_consistency",
        "unpaid_past_due_bills", "current_outstanding_LKR", "current_overdue_days",
        "collection_actions", "suspensions",
    ]
    for key in numeric:
        customers[key] = pd.to_numeric(customers[key], errors="coerce")
    required_nonnull = [k for k in numeric if k != "avg_payment_delay_days"]
    # payment_consistency may be missing only when there is no settled history.
    required_nonnull.remove("payment_consistency")
    if customers[required_nonnull].isna().any().any():
        raise ValueError("Invalid or missing required numeric values in Step 13 output")
    must_be_nonnegative = [
        "settled_bills", "unpaid_past_due_bills", "current_outstanding_LKR",
        "current_overdue_days", "collection_actions", "suspensions",
    ]
    if (customers[must_be_nonnegative] < 0).any().any():
        raise ValueError("Negative quantities found in Step 13 results")
    accepted = {"Regular payer", "Predictable late payer", "Irregular payer", "High-risk non-payer"}
    if not customers["behaviour_classification"].isin(accepted).all():
        raise ValueError("Unexpected classification in Step 13 output")
    # Avoid silently treating internally contradictory arrears as no overdue.
    ambiguous = (
        (customers["unpaid_past_due_bills"] > 0)
        & ((customers["current_overdue_days"] <= 0) | (customers["current_outstanding_LKR"] <= 0))
    ) | (
        (customers["current_overdue_days"] > 0)
        & ((customers["unpaid_past_due_bills"] <= 0) | (customers["current_outstanding_LKR"] <= 0))
    )
    if ambiguous.any():
        raise ValueError(f"Contradictory overdue fields for {int(ambiguous.sum())} account(s); review data first")

    decisions = customers.apply(recommend, axis=1, args=(thresholds,))
    out = pd.concat([customers, decisions], axis=1)
    ordering = {"Priority follow-up": 0, "Normal follow-up": 1, "No follow-up": 2}
    out["action_sort"] = out["recommended_action"].map(ordering)
    out = out.sort_values(
        ["action_sort", "current_overdue_days", "current_outstanding_LKR", "account_no"],
        ascending=[True, False, False, True], kind="stable",
    ).drop(columns=["action_sort"]).reset_index(drop=True)
    out["priority_rank"] = pd.NA
    selected = out["recommended_action"].ne("No follow-up") & out["currently_overdue"]
    out.loc[selected, "priority_rank"] = range(1, int(selected.sum()) + 1)
    out["analysis_date"] = snapshot

    OUTPUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(ALL_REPORT, index=False)
    # Call-list candidate: only ACTIVE overdue accounts requiring follow-up.
    # Irregular accounts with no past-due bill are monitoring cases, not calls.
    followup = out.loc[selected].copy()
    followup.to_csv(FOLLOWUP_REPORT, index=False)

    lines = [
        "SLT — EXPLAINABLE COLLECTION RECOMMENDATIONS (DRAFT)",
        f"Snapshot date: {snapshot}",
        f"Accounts assessed: {len(out)}",
        "Source: Step 13 classification data; workbook thresholds used only as draft rules",
        "Reference recommended_action labels NOT used to determine actions.",
        "",
        "Recommendation counts:",
        out["recommended_action"].value_counts().to_string(),
        "",
        f"Currently overdue accounts: {int(out['currently_overdue'].sum())}",
        f"Overdue accounts flagged for follow-up: {len(followup)}",
        f"Overdue accounts allowed temporary grace: {int((out['currently_overdue'] & out['recommended_action'].eq('No follow-up')).sum())}",
        "",
        "Draft thresholds:",
        f"  Priority if overdue days >= {thresholds['priority_days']:g}, or unpaid past-due bills >= {thresholds['priority_bills']:g}",
        f"  Reliable late-payer grace: usual positive delay + {thresholds['grace_days']:g} days",
        f"  No-follow-up limit when overdue: LKR {thresholds['max_no_followup_balance']:,.2f}",
        "",
        "Safety: 'Priority follow-up' is NOT an automated call or service action.",
        "Human review and SLT business approval required before operational use.",
    ]

    # Compare with the workbook designer's reference actions AFTER the decisions,
    # to identify cases requiring business review. Never use labels as features.
    reference = pd.read_excel(
        WORKBOOK, sheet_name="Behaviour_Summary",
        usecols=["account_no", "recommended_action"], engine="openpyxl",
        dtype={"account_no": "string"},
    )
    compare = out[["account_no", "recommended_action"]].merge(
        reference.rename(columns={"recommended_action": "workbook_reference_action"}),
        on="account_no", how="left", validate="one_to_one",
    )
    # The workbook uses presentation variants (e.g. "No immediate collection")
    # that map to the same three operational categories.
    aliases = {
        "No follow-up": "No follow-up",
        "No immediate collection": "No follow-up",
        "Normal follow-up": "Normal follow-up",
        "Normal follow-up (guardrail)": "Normal follow-up",
        "Priority follow-up": "Priority follow-up",
    }
    compare["normalized_reference_action"] = compare["workbook_reference_action"].map(aliases)
    mismatches = compare.loc[
        compare["recommended_action"] != compare["normalized_reference_action"]
    ]
    lines += [
        "",
        f"Agreement with workbook's normalized illustrative reference actions: {len(compare) - len(mismatches)}/{len(compare)}",
        "Different wording normalized (No immediate collection / Normal follow-up guardrail).",
        "Differences are for review; reference agreement is NOT ML accuracy.",
        f"Differences: {len(mismatches)}",
    ]
    if not mismatches.empty:
        lines += ["", "First 20 differences:", mismatches.head(20).to_string(index=False)]
    VALIDATION_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("SLT COLLECTION RECOMMENDATIONS — DRAFT")
    print("Analysis date:", snapshot)
    print("Accounts:", len(out))
    print(out["recommended_action"].value_counts().to_string())
    print("Currently overdue:", int(out["currently_overdue"].sum()))
    print("Overdue follow-up candidates:", len(followup))
    print("Saved:", ALL_REPORT)
    print("Saved:", FOLLOWUP_REPORT)
    print("Validation:", VALIDATION_REPORT)
    print("IMPORTANT: Draft recommendations only; business approval and human review required.")


if __name__ == "__main__":
    main()
