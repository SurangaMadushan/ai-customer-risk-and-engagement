"""Step 29 — Prepare private account-month billing indicators (NO risk scores).

Usage from the SLT_Payment_Project root:
    python -m pip install duckdb
    python 29_prepare_account_month_features.py --sample-rows 20000
    python 29_prepare_account_month_features.py

Reads ONLY data/historical/DIM_BILL_YYYY_masked.parquet. Creates a PRIVATE
DuckDB account-month database plus aggregate reports under outputs/private_audit.
Do NOT commit or publish that folder. Do NOT use account-month indicators as
confirmed invoice settlements or future non-payment labels. Original Parquet
files, Flask website and existing scores are not changed.

Important: The 2025 file is not required. Each calendar year is processed
separately; this does not assume cross-year account masking is stable.
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
SOURCE = ROOT / "data" / "historical"
OUT = ROOT / "outputs" / "private_audit"
REQUIRED = {"account_no", "bill_dtm", "invoice_net_amt", "payments_amt", "outstanding_debt_amt", "balance_fwd_amt"}


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create private monthly descriptive billing features")
    parser.add_argument("--sample-rows", type=int, default=0,
                        help="Process first N rows PER FILE for a quick smoke test (0 = full)")
    parser.add_argument("--memory-limit", default="1GB",
                        help="DuckDB memory limit such as 1GB or 2048MB")
    args = parser.parse_args()
    if args.sample_rows < 0:
        parser.error("--sample-rows must be nonnegative")
    if not re.fullmatch(r"\d+(?:GB|MB)", args.memory_limit.upper()):
        parser.error("--memory-limit must look like 1GB or 2048MB")
    try:
        import duckdb
    except ImportError:
        print("DuckDB missing. Install with: python -m pip install duckdb")
        return 1

    paths = []
    for path in SOURCE.glob("DIM_BILL_*_masked.parquet"):
        match = re.fullmatch(r"DIM_BILL_(\d{4})_masked\.parquet", path.name)
        if match:
            paths.append((int(match.group(1)), path))
    paths.sort()
    if not paths:
        print(f"No billing Parquet files found in: {SOURCE}")
        return 1

    tag = "sample" if args.sample_rows else "full"
    OUT.mkdir(parents=True, exist_ok=True)
    db_path = OUT / f"step29_private_account_month_{tag}.duckdb"
    temp_path = OUT / f"step29_temp_{tag}"
    temp_path.mkdir(parents=True, exist_ok=True)

    print("\nSLT STEP 29 — MONTHLY ACCOUNT BILLING INDICATORS")
    print("Mode:", tag.upper(), "| Files:", len(paths))
    print("This does NOT train a model, assign creditworthiness weights, or label non-payers.")
    print("Masked account IDs will NOT be printed or exported to CSV.")
    print("2025 is not required. Account IDs are not joined between years.")
    print("Private database:", db_path.relative_to(ROOT), flush=True)

    con = None
    summary = []
    monthly_overview = []
    try:
        con = duckdb.connect(str(db_path))
        con.execute(f"SET memory_limit='{args.memory_limit.upper()}'")
        con.execute("SET threads=2")
        con.execute("SET preserve_insertion_order=false")
        con.execute("SET temp_directory='" + temp_path.as_posix().replace("'", "''") + "'")
        for year, p in paths:
            print(f"\nProcessing {p.name} ...", flush=True)
            src = "read_parquet('" + p.as_posix().replace("'", "''") + "')"
            columns = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
            if REQUIRED - columns:
                raise ValueError(f"{p.name} missing columns: {sorted(REQUIRED - columns)}")
            if args.sample_rows:
                src = f"(SELECT * FROM {src} LIMIT {args.sample_rows})"

            # IMPORTANT: months are based on bill_dtm, not payment dates.
            # Sums are descriptive row-level values, NOT verified financial balances.
            table = f"account_month_{year}"
            con.execute(f"""
                CREATE OR REPLACE TABLE {table} AS
                WITH raw_rows AS (
                    SELECT TRIM(CAST(account_no AS VARCHAR)) AS masked_account_id,
                           TRY_CAST(bill_dtm AS DATE) AS bill_date,
                           TRY_CAST(invoice_net_amt AS DOUBLE) AS billed_value,
                           TRY_CAST(payments_amt AS DOUBLE) AS payment_value_unverified,
                           TRY_CAST(outstanding_debt_amt AS DOUBLE) AS debt_value_unverified,
                           TRY_CAST(balance_fwd_amt AS DOUBLE) AS balance_forward_unverified
                    FROM {src}
                )
                SELECT masked_account_id,
                       CAST(date_trunc('month', bill_date) AS DATE) AS bill_month,
                       COUNT(*)::BIGINT AS billing_record_count,
                       SUM(COALESCE(billed_value, 0)) AS billed_row_amount_sum,
                       AVG(billed_value) AS billed_row_amount_mean,
                       SUM(COALESCE(payment_value_unverified, 0)) AS payment_row_amount_sum_UNVERIFIED,
                       COUNT(*) FILTER (WHERE debt_value_unverified > 0)::BIGINT AS positive_debt_row_count,
                       MAX(debt_value_unverified) AS maximum_debt_row_value_UNVERIFIED,
                       COUNT(*) FILTER (WHERE billed_value < 0)::BIGINT AS negative_invoice_row_count,
                       COUNT(*) FILTER (WHERE payment_value_unverified < 0)::BIGINT AS negative_payment_row_count,
                       COUNT(*) FILTER (WHERE balance_forward_unverified < 0)::BIGINT AS negative_balance_forward_row_count
                FROM raw_rows
                WHERE masked_account_id IS NOT NULL AND masked_account_id <> ''
                  AND bill_date IS NOT NULL
                GROUP BY 1, 2
            """)
            # Aggregate only: do not print or export any masked account IDs.
            agg = con.execute(f"""
                SELECT COUNT(*)::BIGINT AS account_month_rows,
                       COUNT(DISTINCT masked_account_id)::BIGINT AS distinct_accounts,
                       COALESCE(SUM(billing_record_count), 0)::BIGINT AS represented_billing_rows,
                       COUNT(*) FILTER (WHERE positive_debt_row_count > 0)::BIGINT AS account_months_with_positive_debt_row,
                       COUNT(*) FILTER (WHERE negative_invoice_row_count > 0)::BIGINT AS account_months_with_negative_invoice_row,
                       MIN(bill_month) AS first_bill_month,
                       MAX(bill_month) AS last_bill_month
                FROM {table}
            """).fetchone()
            fields = ["year", "account_month_rows", "distinct_accounts", "represented_billing_rows",
                      "account_months_with_positive_debt_row", "account_months_with_negative_invoice_row",
                      "first_bill_month", "last_bill_month"]
            row = dict(zip(fields, [year, *agg]))
            row["first_bill_month"] = str(row["first_bill_month"])
            row["last_bill_month"] = str(row["last_bill_month"])
            summary.append(row)
            print("  Account-month rows:", f"{row['account_month_rows']:,}")
            print("  Distinct masked accounts:", f"{row['distinct_accounts']:,}")
            print("  Represented billing records:", f"{row['represented_billing_rows']:,}")
            print("  Account-months with any positive outstanding row:",
                  f"{row['account_months_with_positive_debt_row']:,}")

            for bill_month, account_months, billing_rows, month_debt_accounts in con.execute(f"""
                SELECT bill_month,
                       COUNT(*)::BIGINT,
                       COALESCE(SUM(billing_record_count),0)::BIGINT,
                       COUNT(*) FILTER (WHERE positive_debt_row_count > 0)::BIGINT
                FROM {table}
                GROUP BY 1
                ORDER BY 1
            """).fetchall():
                monthly_overview.append({
                    "year": year,
                    "bill_month": str(bill_month),
                    "account_month_rows": int(account_months),
                    "billing_records": int(billing_rows),
                    "account_months_with_positive_debt_row": int(month_debt_accounts),
                })

        write_csv(OUT / f"step29_yearly_account_month_summary_{tag}.csv", summary)
        write_csv(OUT / f"step29_monthly_account_activity_{tag}.csv", monthly_overview)
        notes = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": tag,
            "years_processed": [year for year, _ in paths],
            "sample_rows_per_file": args.sample_rows,
            "private_account_month_database": db_path.name,
            "account_month_tables": [f"account_month_{year}" for year, _ in paths],
            "summary_by_year": summary,
            "limitations": [
                "These are account-month DESCRIPTIVE indicators only; not credit scores, defaults or unpaid invoices.",
                "No payment transaction date exists in source; payment delays cannot be calculated.",
                "Data-owner definitions of payments_amt, outstanding_debt_amt and row grain remain unconfirmed.",
                "Do not treat sums of debt snapshot rows as unique money owed.",
                "Account masking consistency across years is unverified; no cross-year join performed.",
                "No future non-payment outcome was defined or estimated.",
                "2025 was not required; 2026 is a partial year.",
                "Sample mode uses the first N file rows, not a representative customer sample.",
                "Account-level DuckDB file is PRIVATE and should not be committed to GitHub.",
            ],
        }
        (OUT / f"step29_monthly_feature_notes_{tag}.json").write_text(
            json.dumps(notes, indent=2, default=str), encoding="utf-8"
        )
        print("\nSaved aggregate CSV: outputs/private_audit/step29_yearly_account_month_summary_" + tag + ".csv")
        print("Saved aggregate CSV: outputs/private_audit/step29_monthly_account_activity_" + tag + ".csv")
        print("Saved notes: outputs/private_audit/step29_monthly_feature_notes_" + tag + ".json")
        print("RESULT: Prepared monthly billing indicators; NO risk scores or predicted outcomes generated.")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        print("No source Parquet files or Flask website code were modified.")
        return 1
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
