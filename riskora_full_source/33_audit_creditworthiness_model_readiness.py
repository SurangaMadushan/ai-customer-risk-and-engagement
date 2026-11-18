"""SLT STEP 33 — Creditworthiness feature quality and model-readiness audit.

Purpose: evaluate Step 32 DESCRIPTIVE features before trying to build a
one-month-ahead payment-risk label or revising the Flask creditworthiness score.

Run from the main SLT_Payment_Project directory:
    python 33_audit_creditworthiness_model_readiness.py --sample
    python 33_audit_creditworthiness_model_readiness.py

Optional for lower/higher available RAM:
    python 33_audit_creditworthiness_model_readiness.py --memory-limit 2GB

Prerequisite: Step 32 completed in the same mode. Uses only local files.

Outputs (AGGREGATE ONLY, stored under outputs/private_audit):
    step33_feature_quality_{sample|full}.csv
    step33_monthly_feature_coverage_{sample|full}.csv
    step33_source_field_audit_{sample|full}.csv
    step33_model_readiness_{sample|full}.md
    step33_model_readiness_{sample|full}.json

Safety / accuracy:
  - Existing Step 32 DB is read-only. No input Parquet or Flask files changed.
  - No account IDs or customer-level records printed/exported to reports.
  - No score, scoring weights, target labels, ML models or predictions.
  - 2025 is not required; no cross-year masked account matching.
  - bill_settled_dtm was missing from inspected 5 Parquet schemas; verify again.
  - Payment/debt fields may not represent point-in-time historical values.
  - Outcomes cannot be inferred from zero payments or positive debt alone.
  - Never commit any private outputs or historical data to public GitHub.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "private_audit"
SOURCE = ROOT / "data" / "historical"
YEARS = (2021, 2022, 2023, 2024, 2026)

REQUIRED = {
    "masked_account_id",
    "feature_month",
    "history_3m_observed_billing_months",
    "history_6m_observed_billing_months",
    "history_3m_complete",
    "history_6m_complete",
    "history_3m_positive_debt_indicator_months",
    "history_6m_positive_debt_indicator_months",
    "history_3m_positive_net_payment_months",
    "history_6m_positive_net_payment_months",
    "history_3m_zero_net_payment_months_AMBIGUOUS",
    "history_3m_debt_indicator_month_share",
    "history_6m_debt_indicator_month_share",
}

QUALITY_FIELDS = [
    "year", "feature_rows", "distinct_masked_accounts", "first_feature_month",
    "last_feature_month", "no_prior_3m_months", "some_prior_3m_months",
    "complete_prior_3m_months", "complete_prior_6m_months",
    "complete_prior_3m_pct", "complete_prior_6m_pct",
    "prior_3m_any_debt_signal", "prior_3m_any_positive_payment_month",
    "prior_3m_any_zero_net_payment_month_AMBIGUOUS",
    "missing_account_or_month", "invalid_history_month_counts",
    "invalid_three_vs_six_history", "invalid_debt_indicator_counts",
    "invalid_payment_indicator_counts", "invalid_debt_indicator_shares",
    "incorrect_complete_history_flags", "quality_anomaly_total",
]

MONTHLY_FIELDS = [
    "year", "feature_month", "feature_rows", "complete_prior_3m_months",
    "complete_prior_6m_months", "complete_prior_3m_pct",
    "prior_3m_any_debt_signal", "prior_3m_any_positive_payment_month",
]

FIELD_FIELDS = [
    "year", "source_file_found", "bill_settled_dtm_present",
    "payment_due_dtm_present", "payments_amt_present",
    "outstanding_debt_amt_present", "invoice_id_present",
    "payment_transaction_date_field_present", "field_names_checked_metadata_only",
]


def write_csv(path: Path, fields: list[str], items: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)


def source_metadata(con, year: int) -> dict:
    """Read Parquet column metadata only (never read individual data rows)."""
    path = SOURCE / f"DIM_BILL_{year}_masked.parquet"
    row = {
        "year": year,
        "source_file_found": path.is_file(),
        "bill_settled_dtm_present": False,
        "payment_due_dtm_present": False,
        "payments_amt_present": False,
        "outstanding_debt_amt_present": False,
        "invoice_id_present": False,
        "payment_transaction_date_field_present": False,
        "field_names_checked_metadata_only": bool(path.is_file()),
    }
    if not path.is_file():
        return row
    safe_path = path.as_posix().replace("'", "''")
    fields = {x[0].lower() for x in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{safe_path}')"
    ).fetchall()}
    row.update({
        "bill_settled_dtm_present": "bill_settled_dtm" in fields,
        "payment_due_dtm_present": "payment_due_dtm" in fields,
        "payments_amt_present": "payments_amt" in fields,
        "outstanding_debt_amt_present": "outstanding_debt_amt" in fields,
        "invoice_id_present": any(x in fields for x in ("invoice_id", "invoice_no", "bill_id")),
        "payment_transaction_date_field_present": any(
            x in fields for x in ("payment_dtm", "payment_date", "payment_txn_dtm", "payment_posted_dtm")
        ),
    })
    return row


def audit_quality(con, table: str, year: int) -> dict:
    # Aggregation only; exact masked identifiers are never returned.
    # Step 32 uses calendar-month RANGE lookbacks; this query checks internal
    # bounds and completeness, NOT point-in-time availability of payment fields.
    query = f"""
    SELECT
      COUNT(*)::BIGINT AS feature_rows,
      COUNT(DISTINCT masked_account_id)::BIGINT AS distinct_masked_accounts,
      MIN(feature_month) AS first_feature_month,
      MAX(feature_month) AS last_feature_month,
      COUNT(*) FILTER (WHERE history_3m_observed_billing_months = 0)::BIGINT AS no_prior_3m_months,
      COUNT(*) FILTER (WHERE history_3m_observed_billing_months > 0)::BIGINT AS some_prior_3m_months,
      COUNT(*) FILTER (WHERE history_3m_complete)::BIGINT AS complete_prior_3m_months,
      COUNT(*) FILTER (WHERE history_6m_complete)::BIGINT AS complete_prior_6m_months,
      COUNT(*) FILTER (WHERE COALESCE(history_3m_positive_debt_indicator_months, 0) > 0)::BIGINT AS prior_3m_any_debt_signal,
      COUNT(*) FILTER (WHERE COALESCE(history_3m_positive_net_payment_months, 0) > 0)::BIGINT AS prior_3m_any_positive_payment_month,
      COUNT(*) FILTER (WHERE COALESCE(history_3m_zero_net_payment_months_AMBIGUOUS, 0) > 0)::BIGINT AS prior_3m_any_zero_net_payment_month_AMBIGUOUS,
      COUNT(*) FILTER (
          WHERE masked_account_id IS NULL OR TRIM(masked_account_id) = '' OR feature_month IS NULL
      )::BIGINT AS missing_account_or_month,
      COUNT(*) FILTER (
          WHERE history_3m_observed_billing_months NOT BETWEEN 0 AND 3
             OR history_6m_observed_billing_months NOT BETWEEN 0 AND 6
             OR history_3m_observed_billing_months IS NULL
             OR history_6m_observed_billing_months IS NULL
      )::BIGINT AS invalid_history_month_counts,
      COUNT(*) FILTER (
          WHERE history_6m_observed_billing_months < history_3m_observed_billing_months
      )::BIGINT AS invalid_three_vs_six_history,
      COUNT(*) FILTER (
          WHERE COALESCE(history_3m_positive_debt_indicator_months, 0) < 0
             OR COALESCE(history_6m_positive_debt_indicator_months, 0) < 0
             OR COALESCE(history_3m_positive_debt_indicator_months, 0) > history_3m_observed_billing_months
             OR COALESCE(history_6m_positive_debt_indicator_months, 0) > history_6m_observed_billing_months
      )::BIGINT AS invalid_debt_indicator_counts,
      COUNT(*) FILTER (
          WHERE COALESCE(history_3m_positive_net_payment_months, 0) < 0
             OR COALESCE(history_6m_positive_net_payment_months, 0) < 0
             OR COALESCE(history_3m_positive_net_payment_months, 0) > history_3m_observed_billing_months
             OR COALESCE(history_6m_positive_net_payment_months, 0) > history_6m_observed_billing_months
      )::BIGINT AS invalid_payment_indicator_counts,
      COUNT(*) FILTER (
          WHERE history_3m_debt_indicator_month_share < 0
             OR history_3m_debt_indicator_month_share > 1
             OR history_6m_debt_indicator_month_share < 0
             OR history_6m_debt_indicator_month_share > 1
             OR (history_3m_observed_billing_months = 0 AND history_3m_debt_indicator_month_share IS NOT NULL)
             OR (history_6m_observed_billing_months = 0 AND history_6m_debt_indicator_month_share IS NOT NULL)
      )::BIGINT AS invalid_debt_indicator_shares,
      COUNT(*) FILTER (
          WHERE (history_3m_complete IS DISTINCT FROM (history_3m_observed_billing_months = 3))
             OR (history_6m_complete IS DISTINCT FROM (history_6m_observed_billing_months = 6))
      )::BIGINT AS incorrect_complete_history_flags
    FROM {table}
    """
    cursor = con.execute(query)
    fields = [col[0] for col in cursor.description]
    values = list(cursor.fetchone())
    data = dict(zip(fields, values))
    data["first_feature_month"] = str(data["first_feature_month"] or "")
    data["last_feature_month"] = str(data["last_feature_month"] or "")
    total = int(data["feature_rows"])
    data["complete_prior_3m_pct"] = round(100 * int(data["complete_prior_3m_months"]) / total, 2) if total else 0
    data["complete_prior_6m_pct"] = round(100 * int(data["complete_prior_6m_months"]) / total, 2) if total else 0
    error_names = [
        "missing_account_or_month", "invalid_history_month_counts",
        "invalid_three_vs_six_history", "invalid_debt_indicator_counts",
        "invalid_payment_indicator_counts", "invalid_debt_indicator_shares",
        "incorrect_complete_history_flags",
    ]
    # Sum of check-fail counts, NOT necessarily number of unique problem rows.
    data["quality_anomaly_total"] = sum(int(data[k]) for k in error_names)
    return {"year": year, **data}


def audit_monthly(con, table: str, year: int) -> list[dict]:
    cursor = con.execute(f"""
      SELECT feature_month,
             COUNT(*)::BIGINT AS feature_rows,
             COUNT(*) FILTER (WHERE history_3m_complete)::BIGINT AS complete_prior_3m_months,
             COUNT(*) FILTER (WHERE history_6m_complete)::BIGINT AS complete_prior_6m_months,
             COUNT(*) FILTER (WHERE COALESCE(history_3m_positive_debt_indicator_months,0) > 0)::BIGINT AS prior_3m_any_debt_signal,
             COUNT(*) FILTER (WHERE COALESCE(history_3m_positive_net_payment_months,0) > 0)::BIGINT AS prior_3m_any_positive_payment_month
      FROM {table}
      GROUP BY feature_month
      ORDER BY feature_month
    """)
    result = []
    for month, count, m3, m6, debt, payment in cursor.fetchall():
        result.append({
            "year": year,
            "feature_month": str(month),
            "feature_rows": count,
            "complete_prior_3m_months": m3,
            "complete_prior_6m_months": m6,
            "complete_prior_3m_pct": round(100 * m3 / count, 2) if count else 0,
            "prior_3m_any_debt_signal": debt,
            "prior_3m_any_positive_payment_month": payment,
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 33 — audit creditworthiness candidate features; no scoring")
    parser.add_argument("--sample", action="store_true", help="Use Step 32 sample database")
    parser.add_argument("--memory-limit", default="1GB", help="DuckDB RAM limit, e.g. 1GB or 2GB")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+(?:MB|GB)", args.memory_limit.upper()):
        parser.error("--memory-limit must look like 1GB or 2048MB")

    try:
        import duckdb
    except ImportError:
        print("ERROR: DuckDB is missing. Run: python -m pip install duckdb")
        return 1

    mode = "sample" if args.sample else "full"
    db = OUT / f"step32_private_creditworthiness_features_{mode}.duckdb"
    if not db.is_file():
        print("ERROR: Step 32 private database was not found:", db)
        print("Run Step 32 in the same mode first.")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    quality_rows: list[dict] = []
    month_rows: list[dict] = []
    field_rows: list[dict] = []
    con = None

    print("\nSLT STEP 33 — CREDITWORTHINESS FEATURE QUALITY & MODEL READINESS")
    print("Mode:", mode.upper(), "| Years:", ", ".join(str(y) for y in YEARS))
    print("Reading Step 32 private database only. No credit score or non-payment labels are created.")
    print("No masked customer identifiers will be printed or exported.", flush=True)

    try:
        con = duckdb.connect(str(db), read_only=True)
        con.execute(f"SET memory_limit='{args.memory_limit.upper()}'")
        con.execute("SET threads=2")
        con.execute("SET preserve_insertion_order=false")
        present = {r[0] for r in con.execute("SHOW TABLES").fetchall()}

        for year in YEARS:
            table = f"credit_features_{year}"
            if table not in present:
                raise ValueError(f"Missing table {table} in {db.name}. Rerun Step 32 ({mode}).")
            cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {table}").fetchall()}
            missing = REQUIRED - cols
            if missing:
                raise ValueError(f"Table {table} is missing expected features: {sorted(missing)}")

            print(f"\nAuditing {year} ...", flush=True)
            one = audit_quality(con, table, year)
            quality_rows.append(one)
            month_rows.extend(audit_monthly(con, table, year))
            field_rows.append(source_metadata(con, year))
            print("  Feature records:", f"{one['feature_rows']:,}")
            print("  Complete 3-month histories:", f"{one['complete_prior_3m_months']:,}",
                  f"({one['complete_prior_3m_pct']:.2f}%)")
            print("  Complete 6-month histories:", f"{one['complete_prior_6m_months']:,}",
                  f"({one['complete_prior_6m_pct']:.2f}%)")
            print("  Feature consistency check-fail count (may overlap):",
                  f"{one['quality_anomaly_total']:,}")
            print("  bill_settled_dtm in raw file:", field_rows[-1]["bill_settled_dtm_present"])

        quality_csv = OUT / f"step33_feature_quality_{mode}.csv"
        month_csv = OUT / f"step33_monthly_feature_coverage_{mode}.csv"
        field_csv = OUT / f"step33_source_field_audit_{mode}.csv"
        report_path = OUT / f"step33_model_readiness_{mode}.md"
        json_path = OUT / f"step33_model_readiness_{mode}.json"
        write_csv(quality_csv, QUALITY_FIELDS, quality_rows)
        write_csv(month_csv, MONTHLY_FIELDS, month_rows)
        write_csv(field_csv, FIELD_FIELDS, field_rows)

        issues = sum(r["quality_anomaly_total"] for r in quality_rows)
        settle_present = all(r["bill_settled_dtm_present"] for r in field_rows)
        total = sum(r["feature_rows"] for r in quality_rows)
        enough3 = sum(r["complete_prior_3m_months"] for r in quality_rows)
        enough6 = sum(r["complete_prior_6m_months"] for r in quality_rows)
        status = "HOLD — outcome definition and point-in-time data must be confirmed"
        report = [
            "# SLT Step 33 — Creditworthiness model-readiness review",
            "",
            f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}",
            f"Mode: **{mode.upper()}**",
            f"**READINESS: {status}**",
            "",
            "## Aggregated feature coverage",
            f"- Candidate feature rows: **{total:,}** (not distinct people).",
            f"- Rows with 3 complete prior calendar billing months: **{enough3:,}**.",
            f"- Rows with 6 complete prior calendar billing months: **{enough6:,}**.",
            f"- Feature consistency check-fail count: **{issues:,}** (checks can overlap).",
            "- These coverage figures are descriptive; they are not model accuracy or an SLT KPI.",
            "",
            "## Source field availability",
            f"- `bill_settled_dtm` present in every source Parquet file: **{settle_present}**.",
            "- Check `step33_source_field_audit_" + mode + ".csv` for each year.",
            "- Even if a settlement-date column is present, its meaning, invoice join key and event-time semantics still require approval.",
            "",
            "## What this audit does and does not demonstrate",
            "- Checks feature history coverage, internal numerical bounds, and descriptive monthly coverage.",
            "- Does NOT prove payment values were historically available as of the prediction date.",
            "- Does NOT establish whether debt was a snapshot, per-bill amount, or retrospectively updated amount.",
            "- Does NOT establish exact bill settlement dates or reliable unpaid-by-cutoff labels.",
            "- Does NOT generate credit scores, data-driven weights, non-payment outcomes or ML performance results.",
            "- Each year is audited independently. No cross-year customer matching and no need for a 2025 file.",
            "- Sample mode reads the earlier Step 32 sample (first source rows), NOT a representative random sample.",
            "",
            "## BLOCKERS before supervised scoring or predicting the next month",
            "1. Locate `bill_settled_dtm` or dated payment transactions and confirm the data owner-approved join keys, coverage, and settlement meaning.",
            "2. Agree the exact **one-month-ahead** outcome: e.g. end-of-next-calendar-month unpaid status or unpaid 30 days after due date; these are different definitions.",
            "3. Validate invoice/billing row grain and whether historical payment/debt amounts are point-in-time or updated later.",
            "4. Confirm account masking stability for any longitudinal link across years; meanwhile do not cross-year join.",
            "5. Rebuild training features using only information actually available at each prediction cutoff; use chronological validation and test/calibrate risk probabilities.",
            "6. Have SLT review fairness, operational thresholds, scoring explanations, and human oversight before production use.",
            "",
            "## Recommended next engineering work",
            "- Step 34 should begin with a **settlement-date/payment-ledger data contract** or a documented as-of data source, NOT arbitrary score optimization.",
            "- Keep existing Flask score labeled `PROVISIONAL / NOT STATISTICALLY VALIDATED`.",
            "- Keep all raw data and private DuckDB files outside public GitHub.",
            "",
        ]
        report_path.write_text("\n".join(report), encoding="utf-8")
        payload = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "readiness": status,
            "quality_check_fail_count_may_overlap": issues,
            "candidate_feature_rows": total,
            "complete_prior_3m": enough3,
            "complete_prior_6m": enough6,
            "settlement_field_present_in_all_sources": settle_present,
            "quality_by_year": quality_rows,
            "source_field_availability_by_year": field_rows,
            "limitations": [
                "No one-month future payment outcome defined or validated",
                "No guaranteed point-in-time features or verified settlement event history",
                "Unverified stable masking across years",
                "No statistically justified factor weights or validated creditworthiness score",
            ],
        }
        json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

        print("\nSaved:")
        for f in (quality_csv, month_csv, field_csv, report_path, json_path):
            print(" ", f.relative_to(ROOT))
        print("RESULT: Feature quality audit completed. Readiness remains HOLD pending confirmed future labels.")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        print("Existing databases, raw Parquet files and Flask site were not modified.")
        return 1
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
