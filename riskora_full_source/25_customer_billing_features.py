"""SLT Step 25: private account-year billing features and cross-year ID-linkage check.

Run from the existing SLT_Payment_Project root:
    python -m pip install duckdb
    python 25_customer_billing_features.py --sample-rows 20000
    python 25_customer_billing_features.py

No Flask files, source Parquet files, scoring weights, or production customer
records are modified. This tool CREATES LOCAL ACCOUNT-LEVEL DATA in a private
DuckDB file; NEVER upload that database or its output directory to GitHub.

IMPORTANT: Historical per-row outstanding/payment amounts are NOT verified
invoice-level settlements or future nonpayment labels. No predictive model,
creditworthiness score or delinquency label is created by this script.
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
EXPECTED = {
    "account_no", "bill_dtm", "payment_due_dtm", "invoice_net_amt",
    "payments_amt", "outstanding_debt_amt", "balance_fwd_amt",
}


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Private, disk-backed SLT account-year feature preparation")
    ap.add_argument("--sample-rows", type=int, default=0,
                    help="Use first N rows PER FILE to verify operation; omit for full data")
    ap.add_argument("--memory-limit", type=str, default="1GB",
                    help="DuckDB maximum memory, e.g., 1GB or 2GB (default 1GB)")
    args = ap.parse_args()
    if args.sample_rows < 0:
        ap.error("--sample-rows must be zero or greater")
    if not re.fullmatch(r"\d+(?:MB|GB)", args.memory_limit, re.IGNORECASE):
        ap.error("--memory-limit example: 1GB or 2048MB")
    try:
        import duckdb
    except ImportError:
        print("Missing DuckDB package. Install: python -m pip install duckdb")
        return 1

    matches = []
    for p in SOURCE.glob("DIM_BILL_*_masked.parquet"):
        m = re.fullmatch(r"DIM_BILL_(\d{4})_masked\.parquet", p.name)
        if m:
            matches.append((int(m.group(1)), p))
    matches.sort()
    if not matches:
        print("No historical Parquet files in:", SOURCE)
        return 1

    mode = "SAMPLE" if args.sample_rows else "FULL"
    suffix = "sample" if args.sample_rows else "full"
    OUT.mkdir(parents=True, exist_ok=True)
    db_file = OUT / f"step25_billing_accounts_{suffix}.duckdb"
    temp_path = OUT / f"step25_duckdb_temp_{suffix}"
    temp_path.mkdir(parents=True, exist_ok=True)

    print("\nSLT STEP 25 — ACCOUNT-YEAR BILLING FEATURE PREPARATION")
    print("Mode:", mode)
    print("Billing files:", len(matches))
    print("Memory limit:", args.memory_limit)
    print("Private output:", db_file.relative_to(ROOT))
    print("No customer identifiers will be printed.")
    print("WARNING: This is descriptive billing feature engineering, NOT validated risk scoring.")

    con = None
    try:
        con = duckdb.connect(str(db_file))
        con.execute(f"SET memory_limit='{args.memory_limit.upper()}'")
        con.execute("SET threads=2")
        con.execute("SET preserve_insertion_order=false")
        temp_escaped = temp_path.as_posix().replace("'", "''")
        con.execute(f"SET temp_directory='{temp_escaped}'")
        rows = []
        years = []
        for year, path in matches:
            years.append(year)
            table = f"account_{year}"
            source_sql = "read_parquet('" + path.as_posix().replace("'", "''") + "')"
            # Check schema before processing. No records are fetched.
            colnames = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {source_sql}").fetchall()}
            missing = EXPECTED - colnames
            if missing:
                raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
            source = f"(SELECT * FROM {source_sql} LIMIT {args.sample_rows})" if args.sample_rows else source_sql
            print(f"\nProcessing {path.name} ...", flush=True)
            # Grouping by account_number requires storage; DuckDB can spill to disk.
            con.execute(f"""
                CREATE OR REPLACE TABLE {table} AS
                SELECT
                    TRIM(CAST(account_no AS VARCHAR)) AS account_no,
                    COUNT(*)::BIGINT AS billing_record_count,
                    COUNT(DISTINCT CAST(DATE_TRUNC('month', bill_dtm) AS DATE))::INTEGER AS months_with_billing,
                    MIN(CAST(bill_dtm AS DATE)) AS first_bill_date,
                    MAX(CAST(bill_dtm AS DATE)) AS last_bill_date,
                    AVG(invoice_net_amt) AS mean_invoice_record_amount,
                    SUM(COALESCE(invoice_net_amt, 0)) AS sum_invoice_record_amount,
                    SUM(COALESCE(payments_amt, 0)) AS sum_payment_record_amount_unverified,
                    COUNT(*) FILTER (WHERE outstanding_debt_amt > 0)::BIGINT AS positive_outstanding_record_count,
                    COUNT(*) FILTER (WHERE outstanding_debt_amt < 0)::BIGINT AS negative_outstanding_record_count,
                    MAX(outstanding_debt_amt) AS highest_outstanding_record_amount,
                    COUNT(*) FILTER (WHERE payment_due_dtm > bill_dtm + INTERVAL '365 days')::BIGINT AS unusual_due_date_record_count
                FROM {source}
                WHERE account_no IS NOT NULL
                    AND LENGTH(TRIM(CAST(account_no AS VARCHAR))) > 0
                GROUP BY 1
            """)
            result = con.execute(f"""
                SELECT COUNT(*)::BIGINT, COALESCE(SUM(billing_record_count),0)::BIGINT,
                       COALESCE(SUM(positive_outstanding_record_count),0)::BIGINT,
                       COALESCE(SUM(CASE WHEN positive_outstanding_record_count > 0 THEN 1 ELSE 0 END),0)::BIGINT,
                       COALESCE(SUM(CASE WHEN months_with_billing > 1 THEN 1 ELSE 0 END),0)::BIGINT
                FROM {table}
            """).fetchone()
            row = {
                "year": year,
                "distinct_masked_accounts": int(result[0]),
                "billing_records_with_account_id": int(result[1]),
                "billing_rows_with_positive_outstanding": int(result[2]),
                "accounts_with_any_positive_outstanding_row": int(result[3]),
                "accounts_with_multiple_billing_months": int(result[4]),
                "sample_only": bool(args.sample_rows),
            }
            rows.append(row)
            print("  Distinct masked accounts:", f"{row['distinct_masked_accounts']:,}")
            print("  Records with usable account ID:", f"{row['billing_records_with_account_id']:,}")
            print("  Accounts with positive outstanding on any record:",
                  f"{row['accounts_with_any_positive_outstanding_row']:,}")

        links = []
        for left, right in zip(years, years[1:]):
            common = con.execute(f"""
                SELECT COUNT(*)::BIGINT FROM account_{left} a
                INNER JOIN account_{right} b USING(account_no)
            """).fetchone()[0]
            left_n = next(r["distinct_masked_accounts"] for r in rows if r["year"] == left)
            right_n = next(r["distinct_masked_accounts"] for r in rows if r["year"] == right)
            links.append({
                "from_year": left,
                "to_year": right,
                "matching_masked_account_ids": int(common),
                "percent_of_earlier_accounts": round(100 * int(common) / left_n, 2) if left_n else None,
                "percent_of_later_accounts": round(100 * int(common) / right_n, 2) if right_n else None,
                "gap_in_calendar_years": right - left,
            })
            print(f"  Matching IDs {left} → {right}: {int(common):,}")
        gap = sorted(set(range(years[0], years[-1] + 1)) - set(years))
        write_csv(OUT / f"step25_account_year_summary_{suffix}.csv", rows)
        write_csv(OUT / f"step25_account_id_overlap_{suffix}.csv", links)
        report = {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "sample_first_rows_per_file" if args.sample_rows else "all_rows",
            "sample_rows_per_file": args.sample_rows,
            "yearly": rows,
            "cross_year_id_overlap": links,
            "missing_years": gap,
            "private_account_level_database": db_file.name,
            "warnings": [
                "Results are not a credit score, default probability, or confirmed debtor count.",
                "Outstanding amounts are per billing record; repeated snapshots may occur.",
                "Payments_amt has no transaction date; do not infer exact payment delays.",
                "Billing rows are not necessarily distinct invoices; no invoice ID is present.",
                "Matching masked account IDs across years is suggestive, not proof that the anonymization key is stable.",
                "The 2025 dataset is missing and the 2026 dataset is a partial year.",
                "Sample mode reads earliest rows of each file; it is not a representative sample.",
                "The DuckDB file contains masked account-level data. Never share it publicly or commit to GitHub.",
            ],
        }
        report_path = OUT / f"step25_billing_feature_report_{suffix}.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("\nSummary reports:")
        print(" -", (OUT / f"step25_account_year_summary_{suffix}.csv").relative_to(ROOT))
        print(" -", (OUT / f"step25_account_id_overlap_{suffix}.csv").relative_to(ROOT))
        print(" -", report_path.relative_to(ROOT))
        print("RESULT: Completed", len(years), "years; no risk scores were generated.")
        return 0
    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}")
        print("Keep original Parquet files unchanged. Check DuckDB disk space and file permissions.")
        return 1
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
