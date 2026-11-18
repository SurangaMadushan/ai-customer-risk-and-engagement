"""SLT STEP 32 — Prepare private, DESCRIPTIVE creditworthiness candidate features.

Run in the SLT_Payment_Project root AFTER Step 29:
    python 32_prepare_creditworthiness_features.py --sample
    python 32_prepare_creditworthiness_features.py

Optional for machines with extra RAM:
    python 32_prepare_creditworthiness_features.py --memory-limit 2GB

Inputs (already on your machine):
  outputs/private_audit/step29_private_account_month_sample.duckdb
  outputs/private_audit/step29_private_account_month_full.duckdb

Outputs (ALL private; DO NOT upload to public GitHub):
  outputs/private_audit/step32_private_creditworthiness_features_{mode}.duckdb
  outputs/private_audit/step32_feature_coverage_{mode}.csv
  outputs/private_audit/step32_feature_dictionary_{mode}.md

No individual masked identifiers go into CSVs or terminal output.
No credit scores, factor weights, defaulter labels, or ML predictions are created.

CRITICAL INTERPRETATION:
  * Lookbacks are prior CALENDAR months, not prior observed record positions.
  * 2025 is NOT needed. Accounts are never linked BETWEEN years.
  * Supervisor clarified payments_amt = amount paid and outstanding_debt_amt
    = amount owed. However, payment posting dates and field as-of behaviour
    are not confirmed. These historical indicators are RESEARCH FEATURES, NOT
    proven leakage-free predictions or validated creditworthiness metrics.
  * Rows with zero NET monthly recorded payment amount are NOT proven defaults.
  * Totals of outstanding snapshots are NOT summed into current balances.
  * bill_settled_dtm remains unavailable in the supplied five Parquet schemas.
  * The source Step 29 database and Flask website are READ-ONLY / unchanged.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "outputs" / "private_audit"
YEARS = (2021, 2022, 2023, 2024, 2026)

REQUIRED_COLUMNS = {
    "masked_account_id",
    "bill_month",
    "billing_record_count",
    "billed_row_amount_sum",
    "payment_row_amount_sum_UNVERIFIED",
    "positive_debt_row_count",
    "maximum_debt_row_value_UNVERIFIED",
}

CSV_COLUMNS = [
    "year", "feature_rows", "distinct_masked_accounts", "first_month", "last_month",
    "features_with_any_prior_month", "features_with_complete_3m_history",
    "features_with_complete_6m_history", "features_with_prior_3m_debt_signal",
    "features_with_prior_3m_positive_payment_sum",
]


def save_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def quote_sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def feature_sql(year: int) -> str:
    """Use RANGE on an integer month-index for precise calendar lookbacks.

    Window RANGE 3 PRECEDING / 1 PRECEDING means [month-3, month-1].
    There is one record per masked account and bill_month in Step 29.
    """
    return f"""
    CREATE OR REPLACE TABLE credit_features_{year} AS
    WITH base AS (
        SELECT masked_account_id,
               CAST(bill_month AS DATE) AS feature_month,
               (EXTRACT(YEAR FROM bill_month)::INTEGER * 12
                 + EXTRACT(MONTH FROM bill_month)::INTEGER) AS month_index,
               billing_record_count::BIGINT AS billing_record_count,
               billed_row_amount_sum::DOUBLE AS billed_row_sum,
               payment_row_amount_sum_UNVERIFIED::DOUBLE AS payment_row_sum,
               positive_debt_row_count::BIGINT AS positive_debt_row_count,
               maximum_debt_row_value_UNVERIFIED::DOUBLE AS max_debt_row_value
        FROM source_data.account_month_{year}
        WHERE masked_account_id IS NOT NULL AND bill_month IS NOT NULL
    ), historical AS (
        SELECT masked_account_id, feature_month,
               COUNT(*) OVER w3 AS history_3m_observed_billing_months,
               COUNT(*) OVER w6 AS history_6m_observed_billing_months,
               SUM(billing_record_count) OVER w3 AS history_3m_billing_rows,
               SUM(billing_record_count) OVER w6 AS history_6m_billing_rows,
               SUM(billed_row_sum) OVER w3 AS history_3m_billed_row_sum,
               SUM(billed_row_sum) OVER w6 AS history_6m_billed_row_sum,
               SUM(payment_row_sum) OVER w3 AS history_3m_recorded_payment_row_sum,
               SUM(payment_row_sum) OVER w6 AS history_6m_recorded_payment_row_sum,
               SUM(CASE WHEN payment_row_sum > 0 THEN 1 ELSE 0 END) OVER w3
                   AS history_3m_positive_net_payment_months,
               SUM(CASE WHEN payment_row_sum > 0 THEN 1 ELSE 0 END) OVER w6
                   AS history_6m_positive_net_payment_months,
               SUM(CASE WHEN payment_row_sum = 0 THEN 1 ELSE 0 END) OVER w3
                   AS history_3m_zero_net_payment_months_AMBIGUOUS,
               SUM(CASE WHEN payment_row_sum = 0 THEN 1 ELSE 0 END) OVER w6
                   AS history_6m_zero_net_payment_months_AMBIGUOUS,
               SUM(CASE WHEN positive_debt_row_count > 0 THEN 1 ELSE 0 END) OVER w3
                   AS history_3m_positive_debt_indicator_months,
               SUM(CASE WHEN positive_debt_row_count > 0 THEN 1 ELSE 0 END) OVER w6
                   AS history_6m_positive_debt_indicator_months,
               MAX(max_debt_row_value) OVER w3 AS history_3m_max_outstanding_row_UNVERIFIED,
               MAX(max_debt_row_value) OVER w6 AS history_6m_max_outstanding_row_UNVERIFIED
        FROM base
        WINDOW w3 AS (
                   PARTITION BY masked_account_id ORDER BY month_index
                   RANGE BETWEEN 3 PRECEDING AND 1 PRECEDING),
               w6 AS (
                   PARTITION BY masked_account_id ORDER BY month_index
                   RANGE BETWEEN 6 PRECEDING AND 1 PRECEDING)
    )
    SELECT masked_account_id,
           feature_month,
           history_3m_observed_billing_months,
           history_6m_observed_billing_months,
           (history_3m_observed_billing_months = 3) AS history_3m_complete,
           (history_6m_observed_billing_months = 6) AS history_6m_complete,
           history_3m_billing_rows,
           history_6m_billing_rows,
           history_3m_billed_row_sum,
           history_6m_billed_row_sum,
           history_3m_recorded_payment_row_sum,
           history_6m_recorded_payment_row_sum,
           history_3m_positive_net_payment_months,
           history_6m_positive_net_payment_months,
           history_3m_zero_net_payment_months_AMBIGUOUS,
           history_6m_zero_net_payment_months_AMBIGUOUS,
           history_3m_positive_debt_indicator_months,
           history_6m_positive_debt_indicator_months,
           ROUND(
               history_3m_positive_debt_indicator_months::DOUBLE /
               NULLIF(history_3m_observed_billing_months, 0), 4
           ) AS history_3m_debt_indicator_month_share,
           ROUND(
               history_6m_positive_debt_indicator_months::DOUBLE /
               NULLIF(history_6m_observed_billing_months, 0), 4
           ) AS history_6m_debt_indicator_month_share,
           history_3m_max_outstanding_row_UNVERIFIED,
           history_6m_max_outstanding_row_UNVERIFIED
    FROM historical
    """


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Step 32 — private, descriptive historical creditworthiness candidate features"
    )
    parser.add_argument("--sample", action="store_true",
                        help="Use Step 29's already generated sample database")
    parser.add_argument("--memory-limit", default="1GB",
                        help="DuckDB memory limit, e.g. 1GB or 2GB")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+(?:GB|MB)", args.memory_limit.upper()):
        parser.error("--memory-limit must look like 1GB or 2048MB")

    try:
        import duckdb
    except ImportError:
        print("DuckDB missing. Run: python -m pip install duckdb")
        return 1

    mode = "sample" if args.sample else "full"
    source_db = PRIVATE / f"step29_private_account_month_{mode}.duckdb"
    destination_db = PRIVATE / f"step32_private_creditworthiness_features_{mode}.duckdb"
    if not source_db.is_file():
        print("ERROR: Step 29 private database is missing:", source_db)
        print("Run Step 29 in the same mode first.")
        return 1

    PRIVATE.mkdir(parents=True, exist_ok=True)
    tmp = PRIVATE / f"step32_temp_{mode}"
    tmp.mkdir(parents=True, exist_ok=True)

    print("\nSLT STEP 32 — CREDITWORTHINESS CANDIDATE FEATURE ENGINEERING")
    print("Mode:", mode.upper())
    print("Input:", source_db.relative_to(ROOT))
    print("Output private database:", destination_db.relative_to(ROOT))
    print("No default labels, ML models, credit scores or factor weights are generated.")
    print("No masked account identifiers will be printed or exported to CSV.")
    print("Lookback: previous 3 / 6 CALENDAR months, within each year only.")
    print("Please keep outputs/private_audit out of public GitHub.", flush=True)

    con = None
    rows: list[dict] = []
    try:
        con = duckdb.connect(str(destination_db))
        con.execute(f"SET memory_limit='{args.memory_limit.upper()}'")
        con.execute("SET threads=2")
        con.execute("SET preserve_insertion_order=false")
        con.execute("SET temp_directory='" + quote_sql_path(tmp) + "'")
        con.execute("ATTACH '" + quote_sql_path(source_db) + "' AS source_data (READ_ONLY)")

        for year in YEARS:
            source_name = f"source_data.account_month_{year}"
            try:
                found = {x[0] for x in con.execute(f"DESCRIBE SELECT * FROM {source_name}").fetchall()}
            except Exception as exc:
                raise ValueError(f"Missing expected table {source_name}. Check Step 29 {mode} database.") from exc
            missing = REQUIRED_COLUMNS - found
            if missing:
                raise ValueError(f"{source_name} lacks expected columns: {sorted(missing)}")

            print(f"\nPreparing private features for {year} ...", flush=True)
            con.execute(feature_sql(year))
            values = con.execute(f"""
                SELECT COUNT(*)::BIGINT,
                       COUNT(DISTINCT masked_account_id)::BIGINT,
                       MIN(feature_month), MAX(feature_month),
                       COUNT(*) FILTER (WHERE history_3m_observed_billing_months > 0)::BIGINT,
                       COUNT(*) FILTER (WHERE history_3m_complete)::BIGINT,
                       COUNT(*) FILTER (WHERE history_6m_complete)::BIGINT,
                       COUNT(*) FILTER (WHERE history_3m_positive_debt_indicator_months > 0)::BIGINT,
                       COUNT(*) FILTER (WHERE history_3m_positive_net_payment_months > 0)::BIGINT
                FROM credit_features_{year}
            """).fetchone()
            row = dict(zip(CSV_COLUMNS, [year, *values]))
            row["first_month"] = str(row["first_month"])
            row["last_month"] = str(row["last_month"])
            rows.append(row)
            print("  Feature records:", f"{row['feature_rows']:,}")
            print("  Masked accounts:", f"{row['distinct_masked_accounts']:,}")
            print("  Records with full previous 3-month billing history:",
                  f"{row['features_with_complete_3m_history']:,}")
            print("  Records with full previous 6-month billing history:",
                  f"{row['features_with_complete_6m_history']:,}", flush=True)

        output_csv = PRIVATE / f"step32_feature_coverage_{mode}.csv"
        save_csv(output_csv, rows)
        print("\nSaved aggregate CSV:", output_csv.relative_to(ROOT))

        notes = PRIVATE / f"step32_feature_dictionary_{mode}.md"
        notes.write_text("\n".join([
            "# Step 32 — Creditworthiness candidate feature dictionary",
            "",
            f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}",
            f"Mode: {mode}",
            "",
            "## Source and output",
            f"- Source: `{source_db.name}` (attached read-only).",
            f"- Private output: `{destination_db.name}`, tables `credit_features_YYYY`.",
            "- Every feature row represents a masked account in a billing month, NOT a validated prediction event.",
            "- Feature windows use only billing months within the SAME calendar year.",
            "- An absent billing month is MISSING, not assumed to be a zero-payment month.",
            "",
            "## Features (each computed BEFORE the indicated feature_month)",
            "- `history_3m_observed_billing_months` / `history_6m_observed_billing_months`: count of observed monthly records within the prior 3/6 calendar months.",
            "- `history_3m_complete` / `history_6m_complete`: all 3 or all 6 previous calendar months have a billing-month record.",
            "- `history_*_billing_rows`: historical billing row counts.",
            "- `history_*_billed_row_sum`: descriptive sum of historical billed row amounts.",
            "- `history_*_recorded_payment_row_sum`: descriptive sum of payment amounts recorded on historical billing rows; not verified actual cash received within this time interval.",
            "- `history_*_positive_net_payment_months`: number of observed months whose net recorded payment sum is positive.",
            "- `history_*_zero_net_payment_months_AMBIGUOUS`: number of observed months whose net recorded payment sum equals zero; it is NOT a count of unpaid invoices or default events.",
            "- `history_*_positive_debt_indicator_months`: observed prior months with at least one positive-debt billing row.",
            "- `history_*_debt_indicator_month_share`: positive-debt indicator months / observed billing months (NULL if no history).",
            "- `history_*_max_outstanding_row_UNVERIFIED`: maximum historical row-level outstanding amount, NOT the current outstanding customer balance.",
            "",
            "## Interpretation and safeguards",
            "- `payments_amt = 0` signifies a zero recorded payment at row level as clarified by the supervisor; it is not enough to define future non-payment.",
            "- `outstanding_debt_amt` is an amount owed, but a billing snapshot may be updated after the billing month.",
            "- `bill_settled_dtm` was not available in the five previously inspected Parquet schemas; settlement delay and verified paid-by-cutoff outcomes CANNOT be derived here.",
            "- **Temporal leakage warning:** although windows exclude the feature month, the bill-level fields may not reflect what was actually known at that past time; validate source snapshots/posting times BEFORE using for ML.",
            "- Masked account identifiers have not been verified consistent across years; 2025 is NOT required, and years are processed separately.",
            "- 2026 contains only a partial year. Missing months and gaps restrict lookback coverage.",
            "- These are research features; NO score, score weights, creditworthiness decision, non-payment label, probability, or suspension inference was calculated.",
            "- These private outputs must not be committed to public GitHub or used for customer-facing adverse decisions.",
            "",
            "## Next after Step 32",
            "1. Verify settlement date table (`bill_settled_dtm`) or equivalent as-of payment ledger, field granularity, timestamps, and reliable join keys.",
            "2. Agree on a one-month-ahead payment outcome based on a precisely defined observation and payment cutoff.",
            "3. Check point-in-time feature availability and masking stability, then train/evaluate the model on chronological splits.",
            "4. Only after validation, develop an explainable creditworthiness score and update the Flask website.",
            "",
        ]) + "\n", encoding="utf-8")
        print("Saved notes:", notes.relative_to(ROOT))
        print("RESULT: Feature engineering completed. No score or prediction was generated.")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        print("The original Step 29 database, raw Parquet files and Flask website were not modified.")
        return 1
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
