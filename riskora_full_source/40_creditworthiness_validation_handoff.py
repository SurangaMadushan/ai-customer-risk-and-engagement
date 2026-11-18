#!/usr/bin/env python3
"""SLT STEP 40 — Creditworthiness validation handoff / evidence gate.

Run inside SLT_Payment_Project, after Steps 35–39:
    python 40_creditworthiness_validation_handoff.py

No data download, DuckDB scan, model training, or new packages are needed.
Reads only aggregated private JSON reports produced by earlier scripts.
Does not create customer scores, outcome labels, model probabilities, or
statistically approved weights. No customer IDs or private data are exported.

Purpose: stop the project from treating completed diagnostics and synthetic
label tests as actual predictive weight validation. Creates a concise, actionable
handoff requesting the settlement records and approval needed to continue.

Outputs (do not commit to public GitHub):
  outputs/private_audit/step40_validation_gate.json
  outputs/private_audit/step40_evidence_checklist.csv
  outputs/private_audit/step40_validation_handoff.md
  outputs/private_audit/step40_data_request_to_madam.txt
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "outputs" / "private_audit"

FILES = {
    "feature_evidence": "step35_factor_audit_full.json",
    "statistics": "step36_statistical_diagnostics_full.json",
    "weight_sensitivity": "step37_weight_sensitivity_full.json",
    "source_inventory": "step38_outcome_source_readiness.json",
    "label_tests": "step39_label_logic_and_readiness.json",
}


def load_json(filename: str):
    path = PRIVATE / filename
    if not path.is_file():
        return None, "Missing report"
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return None, "Unexpected report structure"
        return obj, None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"Unreadable report ({type(exc).__name__})"


def positive_integer(value):
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        return 0


def yes_no(value):
    return "YES" if bool(value) else "NO"


def analyze(reports):
    factors = reports.get("feature_evidence") or {}
    stats = reports.get("statistics") or {}
    sensitivity = reports.get("weight_sensitivity") or {}
    source = reports.get("source_inventory") or {}
    labels = reports.get("label_tests") or {}

    n_tests = positive_integer(labels.get("synthetic_test_cases"))
    pass_tests = positive_integer(labels.get("synthetic_test_pass"))
    fail_tests = positive_integer(labels.get("synthetic_test_fail"))
    synthetic_pass = n_tests >= 1 and fail_tests == 0 and pass_tests == n_tests
    candidate_date = len(source.get("timestamp_candidates_found_in") or [])
    candidate_invoice = len(source.get("invoice_id_candidates_found_in") or [])
    cooccurrence = len(source.get("timestamp_and_invoice_id_in_same_file") or [])
    has_source_report = bool(source)

    # Deliberately NEVER infer verified labels or valid weights from the
    # discovery of a column name, from correlations, or synthetic examples.
    validated_real_labels = False
    validated_weights = False

    evidence = [
        ("Historical feature-factor audit", "AVAILABLE" if factors else "MISSING",
         "Descriptive coverage only; not a predictive validation"),
        ("Feature correlation diagnostics", "AVAILABLE" if stats else "MISSING",
         "Associations among predictors do not establish predictive weights"),
        ("Prototype weight sensitivity", "AVAILABLE" if sensitivity else "MISSING",
         "Tests sensitivity on prototype data, not future SLT outcomes"),
        ("Settlement/payment source inventory", "AVAILABLE" if has_source_report else "MISSING",
         "Schema candidates need key, grain, event-time and completeness validation"),
        ("Synthetic one-month target tests", "PASS" if synthetic_pass else "NOT VERIFIED",
         f"{pass_tests}/{n_tests} synthetic scenarios passed; not actual labels"),
        ("Confirmed full settlement / dated payment ledger", "NOT VERIFIED",
         "Unique invoice linkage and complete settlement/payment timeline needed"),
        ("Point-in-time financial features", "NOT VERIFIED",
         "Bill-row amounts may have been updated after historical cutoff"),
        ("Real next-month outcome labels", "NOT VERIFIED",
         "Cannot establish paid-by-cutoff without verified source events"),
        ("Predictive validation of 25/20/15/25/10/5 weights", "NOT VERIFIED",
         "Requires real labels, temporal holdout, calibration and business sign-off"),
    ]
    state = {
        "step": 40,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "HOLD — real-data statistical validation NOT achieved",
        "reports_found": sum(1 for r in reports.values() if r is not None),
        "reports_expected": len(FILES),
        "historical_feature_rows_reported": factors.get("total_feature_rows"),
        "history_complete_3m_rows_reported": factors.get("prior_3m_complete"),
        "history_complete_6m_rows_reported": factors.get("prior_6m_complete"),
        "strong_proxy_correlations_count": stats.get("strong_proxy_correlations_count"),
        "prototype_weight_scenarios_analyzed": sensitivity.get("scenario_count"),
        "source_files_inspected": source.get("inspected_files"),
        "files_with_settlement_or_payment_date_candidate": candidate_date,
        "files_with_invoice_identifier_candidate": candidate_invoice,
        "files_with_both_date_and_invoice_key_candidates": cooccurrence,
        "one_month_label_synthetic_tests_passed": synthetic_pass,
        "one_month_label_test_pass_count": pass_tests,
        "one_month_label_test_total": n_tests,
        "real_one_month_nonpayment_labels_verified": validated_real_labels,
        "creditworthiness_weights_predictively_validated": validated_weights,
        "production_use_approved": False,
        "needs": [
            "SLT-approved target definition: month-end T to month-end T+1, with account and bill eligibility rules",
            "Settlement timestamp bill_settled_dtm or complete dated payment ledger",
            "Verified invoice identifier and invoice-to-payment link (including partial payments and corrections)",
            "Historical as-of completeness and event timestamps; stable masking/identity mapping",
            "SLT business approval of grace periods, disputes/cancellations and minimum unpaid amount",
            "Chronological training/test, precision/recall/PR-AUC, calibration and factor contribution analysis",
        ],
    }
    return state, evidence


def write_outputs(state, evidence, problems):
    PRIVATE.mkdir(parents=True, exist_ok=True)
    (PRIVATE / "step40_validation_gate.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    p_csv = PRIVATE / "step40_evidence_checklist.csv"
    with p_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Evidence item", "Status", "Interpretation"])
        writer.writerows(evidence)

    lines = [
        "# SLT Step 40 — Creditworthiness statistical validation: current decision",
        "",
        f"**Result: {state['decision']}**",
        "",
        "**Scope:** Evidence handoff only. No actual SLT default labels, verified scores or updated scoring weights created.",
        "",
        "## Existing evidence",
        "",
        "| Item | Status | What it means |",
        "|---|---|---|",
    ]
    for item, status, note in evidence:
        lines.append(f"| {item} | {status} | {note} |")
    lines += [
        "",
        "## Report highlights (where available)",
        f"- Historical feature rows in factor audit: {state['historical_feature_rows_reported'] if state['historical_feature_rows_reported'] is not None else 'not reported'}.",
        f"- Completed 3-month billing history rows: {state['history_complete_3m_rows_reported'] if state['history_complete_3m_rows_reported'] is not None else 'not reported'}.",
        f"- Completed 6-month billing history rows: {state['history_complete_6m_rows_reported'] if state['history_complete_6m_rows_reported'] is not None else 'not reported'}.",
        f"- Detected strong feature-proxy correlation pairs: {state['strong_proxy_correlations_count'] if state['strong_proxy_correlations_count'] is not None else 'not reported'}.",
        f"- Prototype sensitivity scenarios: {state['prototype_weight_scenarios_analyzed'] if state['prototype_weight_scenarios_analyzed'] is not None else 'not reported'}.",
        f"- Step 38 files with possible dated settlement/payment fields: {state['files_with_settlement_or_payment_date_candidate']} (schema match alone is not proof).",
        f"- Synthetic outcome logic: {state['one_month_label_test_pass_count']}/{state['one_month_label_test_total']} passed.",
        "",
        "## Next-month payment outcome to approve",
        "At the end of calendar month T, predict whether an eligible account has any qualifying bill",
        "due in T+1 that remains not fully settled at the end of T+1. This is a PROPOSAL",
        "and not the older invoice-level 'unpaid 30 days after due date' definition.",
        "Define thresholds, eligibility and whether bills first issued during T+1 count.",
        "Do not include T+1 bill details in prediction-time input features.",
        "",
        "## What blocks the 100-point weight validation?",
        "- We cannot demonstrate that weights 25/20/15/25/10/5 predict future non-payment",
        "  using descriptive statistics, cross-feature correlations or synthetic tests alone.",
        "- `payments_amt = 0` is not the same as verified non-payment by the future cutoff.",
        "- `outstanding_debt_amt` is amount owed, but historic as-of timing is unknown.",
        "- The five historical Parquet files previously inspected did not contain",
        "  `bill_settled_dtm` or a unique invoice identifier. The data owner may have",
        "  additional tables; re-run Step 38 with --extra-file if authorized data arrives.",
        "",
        "## What SLT should supply / approve next",
    ]
    lines += [f"{idx}. {need}" for idx, need in enumerate(state['needs'], 1)]
    if problems:
        lines += ["", "## Missing or unreadable earlier reports"]
        lines += ["- " + problem for problem in problems]
    lines += [
        "",
        "## Decision",
        "The current 100-point creditworthiness scheme is a **provisional prototype**.",
        "Keep the Flask prototype unchanged and do not use this score for automated",
        "production decisions. Once approved dated settlement data and joins are",
        "available, create actual next-month labels and test the model chronologically.",
        "Do not share raw billing or customer-level identifiers outside authorized SLT channels.",
        "",
    ]
    (PRIVATE / "step40_validation_handoff.md").write_text("\n".join(lines), encoding="utf-8")

    message = (
        "Hi Madam,\n\n"
        "We have completed feature preparation, statistical diagnostics, and sensitivity analysis of "
        "our prototype creditworthiness factors. These checks do not yet validate the current "
        "25/20/15/25/10/5 weights against actual future non-payment.\n\n"
        "Could you please help us identify the source table/file containing bill_settled_dtm "
        "(or dated invoice payment transactions), and the unique invoice ID/key needed to "
        "link settlement records with the masked billing data? Please also confirm how "
        "partial payments, adjustments, disputed/cancelled bills and historical snapshots "
        "are recorded.\n\n"
        "For the proposed one-month-ahead target, we plan to assess at the end of month T "
        "whether an eligible bill due in T+1 remains unpaid by the end of T+1. "
        "Could you please confirm this target definition and any grace period?\n\n"
        "We will continue validation using approved internal data only.\n\nThank you, Madam.\n"
    )
    (PRIVATE / "step40_data_request_to_madam.txt").write_text(message, encoding="utf-8")


def main():
    print("\nSLT STEP 40 — CREDITWORTHINESS VALIDATION EVIDENCE GATE")
    print("Reads previous reports only. No customer data, model, or scoring changes.")
    reports = {}
    problems = []
    for key, filename in FILES.items():
        obj, issue = load_json(filename)
        reports[key] = obj
        if issue:
            problems.append(f"{filename}: {issue}")
            print(f"  {filename}: {issue}")
        else:
            print(f"  {filename}: FOUND")
    state, evidence = analyze(reports)
    write_outputs(state, evidence, problems)
    print("\nSynthetic target logic passed:", yes_no(state["one_month_label_synthetic_tests_passed"]))
    print("Predictive weight validation complete:", yes_no(state["creditworthiness_weights_predictively_validated"]))
    print("DECISION:", state["decision"])
    print("\nSaved private reports:")
    for name in (
        "step40_validation_gate.json", "step40_evidence_checklist.csv",
        "step40_validation_handoff.md", "step40_data_request_to_madam.txt",
    ):
        print(" ", (PRIVATE / name).relative_to(ROOT))
    print("\nNEXT: Obtain approved bill_settled_dtm / dated payment records and confirm invoice linkage.")
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
