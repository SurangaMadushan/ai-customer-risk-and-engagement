"""SLT STEP 35 — Audit evidence behind creditworthiness scoring factors.

Run in the SLT_Payment_Project main folder, after Step 32 and Step 34:
    python 35_audit_creditworthiness_factor_evidence.py --sample
    python 35_audit_creditworthiness_factor_evidence.py

Optional:
    python 35_audit_creditworthiness_factor_evidence.py --memory-limit 2GB

INPUTS (read-only):
    outputs/private_audit/step32_private_creditworthiness_features_{mode}.duckdb
    outputs/private_audit/step34_target_readiness_{mode}.json (optional)
    outputs/private_audit/step33_model_readiness_{mode}.json (optional)

OUTPUTS (all aggregate-only and PRIVATE):
    outputs/private_audit/step35_factor_evidence_{mode}.csv
    outputs/private_audit/step35_feature_distributions_{mode}.csv
    outputs/private_audit/step35_feature_history_coverage_{mode}.csv
    outputs/private_audit/step35_creditworthiness_validation_plan_{mode}.md
    outputs/private_audit/step35_factor_audit_{mode}.json

This is NOT a scoring program. It DOES NOT infer individual defaults,
settlement dates, non-payment labels, model accuracy, feature weights, or
creditworthiness probabilities. All six currently used weights stay
PROVISIONAL. No customer identifiers or individual data leave DuckDB.

IMPORTANT: Source payment/debt amounts may be updated after bill dates;
prior bill-month grouping does NOT prove point-in-time information availability.
Missing 2025 is accepted and masked IDs are never joined between years.
Never put raw data or outputs/private_audit on public GitHub.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "private_audit"
YEARS = (2021, 2022, 2023, 2024, 2026)

# Only columns built by Step 32. Values are DESCRIPTIVE, not verified as-of.
FEATURES = [
    ("history_3m_observed_billing_months", "Observed billing months (prior 3 calendar months)", "count"),
    ("history_3m_recorded_payment_row_sum", "Recorded payment amount on prior 3-month billing rows (unverified as-of)", "amount"),
    ("history_3m_positive_net_payment_months", "Prior 3 billing months with a positive net recorded payment indicator", "count"),
    ("history_3m_billed_row_sum", "Billed row amount total in prior 3 calendar months", "amount"),
    ("history_3m_positive_debt_indicator_months", "Prior 3 billing months containing a positive outstanding-debt row", "count"),
    ("history_3m_debt_indicator_month_share", "Share of observed prior 3 months with positive debt-row indicator", "share"),
    ("history_3m_max_outstanding_row_UNVERIFIED", "Maximum positive or non-positive outstanding row amount in prior 3 months (not a current balance)", "amount"),
]
REQUIRED = {"masked_account_id", "feature_month", "history_3m_complete", "history_6m_complete", *[item[0] for item in FEATURES]}

FACTOR_DEFINITIONS = [
    {
        "factor": "Settled bill history", "prototype_max_points": 25,
        "status": "NOT_MEASURABLE_FROM_CURRENT_FILES",
        "available_proxy": "history_3m_positive_net_payment_months",
        "what_is_missing": "Invoice-level settlement status, trustworthy invoice ID, bill_settled_dtm or dated settlement transactions",
        "interpretation": "Positive recorded payments do not prove that invoices were settled",
    },
    {
        "factor": "Payment consistency", "prototype_max_points": 20,
        "status": "PARTIAL_PROXY_ONLY",
        "available_proxy": "history_3m_positive_net_payment_months / history_3m_observed_billing_months",
        "what_is_missing": "Actual payment event dates and due-date-linked invoice settlements",
        "interpretation": "Monthly positive-payment frequency is not on-time payment consistency",
    },
    {
        "factor": "Average payment delay", "prototype_max_points": 15,
        "status": "NOT_MEASURABLE_FROM_CURRENT_FILES",
        "available_proxy": "NONE",
        "what_is_missing": "bill_settled_dtm (or invoice-linked payment dates) and confirmed invoice identifiers",
        "interpretation": "Cannot compute days late using billing-date fields and undated payment amounts",
    },
    {
        "factor": "Current arrears", "prototype_max_points": 25,
        "status": "PARTIAL_HISTORICAL_SIGNAL_ONLY",
        "available_proxy": "history_3m_positive_debt_indicator_months, history_3m_debt_indicator_month_share",
        "what_is_missing": "As-of balance history, invoice-specific overdue status, confirmed amount snapshot timing",
        "interpretation": "A prior positive debt indicator is not a verified current overdue balance",
    },
    {
        "factor": "Outstanding exposure", "prototype_max_points": 10,
        "status": "PARTIAL_HISTORICAL_SIGNAL_ONLY",
        "available_proxy": "history_3m_max_outstanding_row_UNVERIFIED",
        "what_is_missing": "Approved account balance definition, as-of timestamps, row grain and exposure aggregation rule",
        "interpretation": "Maximum prior debt-row amount is not the sum owed currently by an account",
    },
    {
        "factor": "Collection history", "prototype_max_points": 5,
        "status": "NOT_MEASURABLE_FROM_CURRENT_FILES",
        "available_proxy": "NONE",
        "what_is_missing": "Dated collection actions / suspension history and customer or service linkage",
        "interpretation": "Billing files do not supply collection contacts or confirmed suspension events",
    },
]

FACTOR_COLUMNS = ["factor", "prototype_max_points", "status", "available_proxy", "what_is_missing", "interpretation"]
COVERAGE_COLUMNS = [
    "year", "feature_rows", "has_any_prior_3m_history", "complete_prior_3m_history",
    "complete_prior_6m_history", "complete_prior_3m_pct", "complete_prior_6m_pct",
    "prior_3m_positive_payment_indicator", "prior_3m_positive_debt_indicator",
    "prior_3m_payment_value_missing", "prior_3m_debt_share_missing",
]
DISTRIBUTION_COLUMNS = [
    "year", "cohort", "feature", "description", "unit", "cohort_rows",
    "non_null", "zero_values", "negative_values", "mean", "p10_approx", "median_approx", "p90_approx",
]


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_json_if_present(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def num(value, digits=4):
    if value is None:
        return None
    v = float(value)
    return round(v, digits) if math.isfinite(v) else None


def coverage_query(table: str) -> str:
    return f"""
    SELECT COUNT(*)::BIGINT AS n,
           COUNT(*) FILTER (WHERE history_3m_observed_billing_months > 0)::BIGINT,
           COUNT(*) FILTER (WHERE history_3m_complete IS TRUE)::BIGINT,
           COUNT(*) FILTER (WHERE history_6m_complete IS TRUE)::BIGINT,
           COUNT(*) FILTER (WHERE history_3m_positive_net_payment_months > 0)::BIGINT,
           COUNT(*) FILTER (WHERE history_3m_positive_debt_indicator_months > 0)::BIGINT,
           COUNT(*) FILTER (
               WHERE history_3m_recorded_payment_row_sum IS NULL
           )::BIGINT,
           COUNT(*) FILTER (
               WHERE history_3m_debt_indicator_month_share IS NULL
           )::BIGINT
    FROM {table}
    """


def distributions_query(table: str, cohort_where: str) -> str:
    """One aggregate scan per cohort/year; no row-level export or account IDs."""
    expressions = ["COUNT(*)::BIGINT AS cohort_rows"]
    for i, (column, _description, _unit) in enumerate(FEATURES):
        # All names originate from the fixed, validated feature list.
        expressions.extend([
            f"COUNT({column})::BIGINT AS f{i}_count",
            f"COUNT(*) FILTER (WHERE {column} = 0)::BIGINT AS f{i}_zeros",
            f"COUNT(*) FILTER (WHERE {column} < 0)::BIGINT AS f{i}_negatives",
            f"AVG(TRY_CAST({column} AS DOUBLE)) AS f{i}_mean",
            f"APPROX_QUANTILE(TRY_CAST({column} AS DOUBLE), 0.10) AS f{i}_p10",
            f"APPROX_QUANTILE(TRY_CAST({column} AS DOUBLE), 0.50) AS f{i}_median",
            f"APPROX_QUANTILE(TRY_CAST({column} AS DOUBLE), 0.90) AS f{i}_p90",
        ])
    return "SELECT\n       " + ",\n       ".join(expressions) + f"\nFROM {table}\nWHERE {cohort_where}"


def markdown_report(mode: str, coverage_rows: list[dict], step34: dict | None) -> str:
    total = sum(row["feature_rows"] for row in coverage_rows)
    complete_3 = sum(row["complete_prior_3m_history"] for row in coverage_rows)
    complete_6 = sum(row["complete_prior_6m_history"] for row in coverage_rows)
    source_status = "Step 34 JSON loaded" if step34 else "Step 34 JSON unavailable; using conservative gate"
    target_status = (str(step34.get("decision", "not supplied")) if step34 else "not confirmed")
    lines = [
        "# SLT Step 35 — Evidence audit for the six creditworthiness factors",
        "",
        f"Generated UTC: {datetime.now(timezone.utc).isoformat()}",
        f"Mode: **{mode.upper()}**",
        "",
        "**MODEL/SCORING READINESS: HOLD. No statistically validated weights or future non-payment labels are available.**",
        "",
        "## Feature coverage (descriptive only)",
        f"- Account-month feature rows: **{total:,}** (not distinct customers).",
        f"- Feature rows with three prior calendar billing months observed: **{complete_3:,}**.",
        f"- Feature rows with six prior calendar billing months observed: **{complete_6:,}**.",
        "- These features are grouped by bill months within each source year; historical account masking stability is not assumed across years.",
        "- Step 32 previous-month windows exclude the current billing month, but recorded payment and outstanding values may have been updated AFTER their billing months. Point-in-time validity is unproven.",
        "- Sample mode may be unrepresentative because prior steps selected the first source records.",
        "",
        "## Status of current prototype scoring factors",
        "| Factor | Provisional maximum | Current evidence |",
        "|---|---:|---|",
    ]
    for factor in FACTOR_DEFINITIONS:
        lines.append(f"| {factor['factor']} | {factor['prototype_max_points']} | {factor['status']} |")
    lines.extend([
        "",
        "**Important:** The listed maximum points add to 100 but are NOT data-derived weights. Do not change them or treat them as validated based on descriptive distributions.",
        "",
        "## Available descriptive signals",
        "- We can inspect 3-month recorded payment activity, billing amount history and positive debt-row frequency.",
        "- These may be candidate predictors later, after the data owner confirms their time semantics.",
        "- We cannot infer settled bill counts, actual days late, collection history or a reliable non-payment outcome from the five billing Parquet schemas alone.",
        "- Do not treat zero payments as a default, or positive outstanding as an unpaid-by-deadline label.",
        "- Do not add outstanding snapshot values over time to produce a current customer balance.",
        "",
        "## Step 34 target-readiness evidence",
        f"- {source_status}.",
        f"- Outcome status: **{target_status}**.",
        "- Proposed decision question: At month-end T, predict whether an eligible account bill due in T+1 remains unpaid by the agreed T+1 cutoff.",
        "- This differs from predicting whether an invoice remains unpaid 30 days AFTER the due date; SLT must select and approve one explicit label definition.",
        "",
        "## What we need to justify the score statistically",
        "1. Obtain `bill_settled_dtm` OR invoice-linked dated payment transactions, verified invoice mapping and permitted access.",
        "2. Confirm whether `payments_amt` and `outstanding_debt_amt` are historical as-of snapshots or retrospectively updated values.",
        "3. Confirm the customer/service identifier linkage, within and (only if verified) across years; 2025 is not required.",
        "4. Approve the one-month outcome, invoice eligibility, partial-payment rules, maturity/grace cutoff, and exclusions.",
        "5. Rebuild point-in-time features from information genuinely available before each scoring cutoff.",
        "6. Establish chronological train/validation/test splits, leaving the final period untouched until model selection is complete.",
        "7. Compare a simple baseline and Logistic Regression with Random Forest; check Precision, Recall, Average Precision, calibration and performance across customer groups.",
        "8. Use out-of-time permutation importance, calibrated coefficients where appropriate, and feature-ablation testing. Correlated features may share importance; importance is NOT itself a 25/20/15 scoring weight.",
        "9. Define explainable mapping to a 0–100 scale only after probability calibration and approval; evaluate score stability, fairness and effects of the collection policy.",
        "10. Keep human review for sensitive collection and creditworthiness decisions; do not automatically block/suspend services based on this unvalidated score.",
        "",
        "## Suggested wording for Tilani Madam",
        "> Our creditworthiness score currently uses six provisional business factors. We have prepared and quality-audited candidate historical billing and payment features. We have not yet assigned statistically supported weights because verified settlement events and point-in-time non-payment labels are not available in the supplied data. We will validate the features against the approved one-month payment outcome once those records are confirmed.",
        "",
        "## Privacy and scope",
        "- CSV and Markdown outputs contain aggregate statistics and field descriptions ONLY. They contain no account identifiers.",
        "- The source Step 32 DuckDB is opened READ-ONLY. No Flask, raw billing files, models or website scores are modified.",
        "- Data from 2021, 2022, 2023, 2024 and 2026 are examined separately; this does NOT depend on acquiring 2025.",
        "- NEVER commit private DuckDB files, raw SLT data or private reports to a public GitHub repository.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 35 — audit evidence for the six provisional creditworthiness factors")
    parser.add_argument("--sample", action="store_true", help="Use the existing Step 32 sample database")
    parser.add_argument("--memory-limit", default="1GB", help="DuckDB RAM limit such as 1GB or 2048MB")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+(?:GB|MB)", args.memory_limit.upper()):
        parser.error("--memory-limit must be 1GB, 2GB, 2048MB, etc.")
    try:
        import duckdb
    except ImportError:
        print("ERROR: DuckDB is not installed. Run: python -m pip install duckdb")
        return 1

    mode = "sample" if args.sample else "full"
    source_db = OUT / f"step32_private_creditworthiness_features_{mode}.duckdb"
    if not source_db.is_file():
        print("ERROR: Cannot find Step 32 database:", source_db)
        print("Run Step 32 in the same mode before running Step 35.")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    step34 = read_json_if_present(OUT / f"step34_target_readiness_{mode}.json")
    step33 = read_json_if_present(OUT / f"step33_model_readiness_{mode}.json")
    coverage_rows: list[dict] = []
    distribution_rows: list[dict] = []

    print("\nSLT STEP 35 — CREDITWORTHINESS FACTOR EVIDENCE AUDIT")
    print("Mode:", mode.upper(), "| Source years:", len(YEARS))
    print("No individual account identifiers, scores, labels or model weights are created.")
    print("Reading Step 32 database without modifying it.", flush=True)
    con = None
    try:
        con = duckdb.connect(str(source_db), read_only=True)
        con.execute(f"SET memory_limit='{args.memory_limit.upper()}'")
        con.execute("SET threads=2")
        con.execute("SET preserve_insertion_order=false")
        present_tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        for year in YEARS:
            table = f"credit_features_{year}"
            if table not in present_tables:
                raise ValueError(f"Step 32 database is missing table {table}")
            available_cols = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {table}").fetchall()}
            if missing := (REQUIRED - available_cols):
                raise ValueError(f"{table} is missing required Step 32 columns: {sorted(missing)}")
            print(f"\nChecking {year} ...", flush=True)
            values = con.execute(coverage_query(table)).fetchone()
            n, any_3, complete_3, complete_6, pos_pay, pos_debt, missing_pay, missing_debt = values
            coverage = dict(zip(COVERAGE_COLUMNS, [
                year, int(n), int(any_3), int(complete_3), int(complete_6),
                round(100 * complete_3 / n, 2) if n else 0.0,
                round(100 * complete_6 / n, 2) if n else 0.0,
                int(pos_pay), int(pos_debt), int(missing_pay), int(missing_debt)
            ]))
            coverage_rows.append(coverage)
            print(f"  Feature rows: {n:,}")
            print(f"  Complete prior-3-month history: {complete_3:,} ({coverage['complete_prior_3m_pct']:.2f}%)")
            print(f"  Positive recorded-payment indicator in prior 3 months: {pos_pay:,}")
            print(f"  Positive outstanding-row indicator in prior 3 months: {pos_debt:,}")

            for cohort, predicate in (
                ("any_prior_3m_billing", "history_3m_observed_billing_months > 0"),
                ("complete_prior_3m_billing", "history_3m_complete IS TRUE"),
            ):
                raw = con.execute(distributions_query(table, predicate)).fetchone()
                group_rows = int(raw[0])
                print(f"  Distribution cohort {cohort}: {group_rows:,} rows", flush=True)
                for i, (feature, label, unit) in enumerate(FEATURES):
                    start = 1 + i * 7
                    count, zero, negatives, mean, p10, median, p90 = raw[start:start + 7]
                    distribution_rows.append({
                        "year": year, "cohort": cohort, "feature": feature,
                        "description": label, "unit": unit, "cohort_rows": group_rows,
                        "non_null": int(count), "zero_values": int(zero),
                        "negative_values": int(negatives), "mean": num(mean),
                        "p10_approx": num(p10), "median_approx": num(median),
                        "p90_approx": num(p90),
                    })

        paths = {
            "factor_evidence_csv": OUT / f"step35_factor_evidence_{mode}.csv",
            "feature_distributions_csv": OUT / f"step35_feature_distributions_{mode}.csv",
            "feature_history_coverage_csv": OUT / f"step35_feature_history_coverage_{mode}.csv",
            "validation_plan_md": OUT / f"step35_creditworthiness_validation_plan_{mode}.md",
            "factor_audit_json": OUT / f"step35_factor_audit_{mode}.json",
        }
        write_csv(paths["factor_evidence_csv"], FACTOR_COLUMNS, FACTOR_DEFINITIONS)
        write_csv(paths["feature_distributions_csv"], DISTRIBUTION_COLUMNS, distribution_rows)
        write_csv(paths["feature_history_coverage_csv"], COVERAGE_COLUMNS, coverage_rows)
        paths["validation_plan_md"].write_text(markdown_report(mode, coverage_rows, step34), encoding="utf-8")
        summary = {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "readiness": "HOLD — outcome labels and point-in-time semantics not verified",
            "total_feature_rows": sum(x["feature_rows"] for x in coverage_rows),
            "prior_3m_complete": sum(x["complete_prior_3m_history"] for x in coverage_rows),
            "prior_6m_complete": sum(x["complete_prior_6m_history"] for x in coverage_rows),
            "years": list(YEARS),
            "prototype_total_max_points": sum(x["prototype_max_points"] for x in FACTOR_DEFINITIONS),
            "source_step33_status": step33.get("readiness") if step33 else "not available",
            "source_step34_status": step34.get("decision") if step34 else "not available",
            "factor_evidence": FACTOR_DEFINITIONS,
            "coverage_by_year": coverage_rows,
            "not_computed": ["Creditworthiness score", "Statistical feature effect on verified default", "Scoring weights", "Non-payment label", "ML evaluation"],
        }
        paths["factor_audit_json"].write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print("\nSaved aggregate-only reports:")
        for p in paths.values():
            print(" ", p.relative_to(ROOT))
        print("\nRESULT: Factor evidence audit completed. Validated scoring remains ON HOLD.")
        print("NEXT: Secure the bill_settled_dtm source / invoice-level dated payments and verify as-of values.")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        print("Step 32 database, historical Parquet files and Flask website were not changed.")
        return 1
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
