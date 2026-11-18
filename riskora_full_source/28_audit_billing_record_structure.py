"""SLT Step 28 — aggregate-only audit of masked billing record structure.

Run in the main SLT_Payment_Project folder:
    python 28_audit_billing_record_structure.py --sample-rows 20000
    python 28_audit_billing_record_structure.py

Dependencies: duckdb (python -m pip install duckdb)
Reads data/historical/DIM_BILL_*_masked.parquet; saves ONLY aggregate
summaries to outputs/private_audit. Does not export IDs, train a model,
set creditworthiness weights, or change the Flask application.

The script checks how many records share an account, product and bill date.
Such repeated keys are NOT necessarily duplicates: a bill may have multiple
valid line items. Ask the data owner to confirm each row's business grain.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "data" / "historical"
OUTPUT_DIR = ROOT / "outputs" / "private_audit"
REQUIRED = {"account_no", "product_serial_num", "bill_dtm", "payment_due_dtm"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Aggregate-only billing row-grain and due-date audit")
    ap.add_argument("--sample-rows", type=int, default=0,
                    help="First N records per file for a quick smoke test; default is full data")
    ap.add_argument("--memory-limit", default="1GB", help="DuckDB RAM limit, e.g. 1GB, 2GB")
    args = ap.parse_args()
    if args.sample_rows < 0:
        ap.error("--sample-rows must be >= 0")
    if not re.fullmatch(r"\d+(?:GB|MB)", args.memory_limit, re.I):
        ap.error("--memory-limit must look like 1GB or 2048MB")

    try:
        import duckdb
    except ImportError:
        print("DuckDB is missing. Run: python -m pip install duckdb")
        return 1

    paths = []
    for p in SOURCE_DIR.glob("DIM_BILL_*_masked.parquet"):
        m = re.fullmatch(r"DIM_BILL_(\d{4})_masked\.parquet", p.name)
        if m:
            paths.append((int(m.group(1)), p))
    paths.sort()
    if not paths:
        print("No Parquet files found under:", SOURCE_DIR)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_DIR / "step28_duckdb_temp"
    tmp.mkdir(parents=True, exist_ok=True)
    mode = "SAMPLE" if args.sample_rows else "FULL"
    tag = "sample" if args.sample_rows else "full"

    print("\nSLT STEP 28 — BILLING RECORD STRUCTURE AUDIT")
    print("Mode:", mode, "| Files:", len(paths))
    print("No individual account identifiers or records will be printed or exported.")
    print("A repeated account+product+bill-date key is NOT proof of a duplicate invoice.")

    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute(f"SET memory_limit='{args.memory_limit.upper()}'")
        conn.execute("SET threads=2")
        conn.execute("SET preserve_insertion_order=false")
        conn.execute("SET temp_directory='" + tmp.as_posix().replace("'", "''") + "'")
        results: list[dict] = []

        for year, p in paths:
            source = "read_parquet('" + p.as_posix().replace("'", "''") + "')"
            columns = {row[0] for row in conn.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
            missing = REQUIRED - columns
            if missing:
                raise ValueError(f"{p.name} missing required fields: {sorted(missing)}")
            if args.sample_rows:
                source = f"(SELECT * FROM {source} LIMIT {args.sample_rows})"
            print(f"\nInspecting {p.name} ...", flush=True)

            # Lightweight field-level and due-date checks. No customer values returned.
            q1 = f"""
            WITH b AS (
                SELECT TRY_CAST(bill_dtm AS DATE) bill_date,
                       TRY_CAST(payment_due_dtm AS DATE) due_date,
                       TRIM(CAST(account_no AS VARCHAR)) account_id,
                       TRIM(CAST(product_serial_num AS VARCHAR)) product_id
                FROM {source}
            )
            SELECT COUNT(*) rows_scanned,
                   COUNT(*) FILTER (WHERE account_id IS NULL OR account_id='') missing_account_rows,
                   COUNT(*) FILTER (WHERE product_id IS NULL OR product_id='') missing_product_rows,
                   COUNT(*) FILTER (WHERE bill_date IS NULL) invalid_bill_date_rows,
                   COUNT(*) FILTER (WHERE due_date IS NULL) invalid_due_date_rows,
                   COUNT(*) FILTER (WHERE due_date < bill_date) due_before_bill_rows,
                   COUNT(*) FILTER (WHERE date_diff('day',bill_date,due_date) BETWEEN 0 AND 30)
                     due_within_30_days_rows,
                   COUNT(*) FILTER (WHERE date_diff('day',bill_date,due_date) BETWEEN 31 AND 60)
                     due_31_to_60_days_rows,
                   COUNT(*) FILTER (WHERE date_diff('day',bill_date,due_date) BETWEEN 61 AND 365)
                     due_61_to_365_days_rows,
                   COUNT(*) FILTER (WHERE date_diff('day',bill_date,due_date) > 365)
                     due_over_365_days_rows
            FROM b
            """
            cur = conn.execute(q1)
            names = [desc[0] for desc in cur.description]
            counts = {name: int(v or 0) for name, v in zip(names, cur.fetchone())}

            # Check the candidate row grain, not actual invoice uniqueness.
            # DuckDB spills grouped data to outputs/private_audit when necessary.
            q2 = f"""
            SELECT COUNT(*) AS account_product_date_groups,
                   COALESCE(SUM(CASE WHEN n > 1 THEN 1 ELSE 0 END),0)
                       AS groups_with_multiple_rows,
                   COALESCE(SUM(CASE WHEN n > 1 THEN n - 1 ELSE 0 END),0)
                       AS extra_rows_on_repeated_keys,
                   COALESCE(MAX(n),0) AS largest_group_size
            FROM (
                SELECT COUNT(*) AS n
                FROM {source}
                WHERE account_no IS NOT NULL
                  AND LENGTH(TRIM(CAST(account_no AS VARCHAR))) > 0
                  AND bill_dtm IS NOT NULL
                GROUP BY TRIM(CAST(account_no AS VARCHAR)),
                         TRIM(CAST(product_serial_num AS VARCHAR)),
                         TRY_CAST(bill_dtm AS DATE)
            ) grouped
            """
            cur = conn.execute(q2)
            names = [desc[0] for desc in cur.description]
            keys = {name: int(v or 0) for name, v in zip(names, cur.fetchone())}
            row = {"year": year, **counts, **keys}
            results.append(row)
            print(f"  Billing rows checked: {row['rows_scanned']:,}")
            print(f"  Repeated account+product+bill-date groups: {row['groups_with_multiple_rows']:,}")
            print(f"  Extra rows within repeated-key groups: {row['extra_rows_on_repeated_keys']:,}")
            print(f"  Due dates 0–30 days after bill: {row['due_within_30_days_rows']:,}")
            print(f"  Due dates over 365 days after bill: {row['due_over_365_days_rows']:,}")

    except Exception as e:
        print(f"AUDIT FAILED: {type(e).__name__}: {e}")
        print("Original billing files were not modified.")
        return 1
    finally:
        conn.close()

    csv_path = OUTPUT_DIR / f"step28_billing_record_structure_{tag}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    report = OUTPUT_DIR / f"step28_billing_structure_notes_{tag}.md"
    lines = [
        "# Step 28 — Billing record structure audit",
        "",
        f"Mode: {mode}; UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        "| Year | Rows | Repeated account+product+bill-date groups | Extra rows within repeated groups | Due >365 days |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in results:
        lines.append(
            f"| {row['year']} | {row['rows_scanned']:,} | "
            f"{row['groups_with_multiple_rows']:,} | "
            f"{row['extra_rows_on_repeated_keys']:,} | "
            f"{row['due_over_365_days_rows']:,} |"
        )
    lines += [
        "", "## Interpretation and next action", "",
        "- This script examines repeated account/product/bill-date combinations. "
        "It cannot tell whether each row is an invoice or a valid product/line item.",
        "- Due-date gaps do not establish when the customer actually paid.",
        "- Step 27 showed the *hypothesized* balance equation matched very few rows. "
        "This does not prove that any input field is incorrect.",
        "- Do not assign validated creditworthiness weights, infer invoice settlements, "
        "or create real non-payment labels from these checks.",
        "- Ask the SLT data owner to confirm the row grain and definitions/as-of dates of "
        "payments_amt, balance_fwd_amt and outstanding_debt_amt.",
        "- The current workflow uses only the available years and does not require 2025.",
        "- Keep this aggregate-only output and source data private to the approved environment.",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nSaved aggregate CSV:", csv_path.relative_to(ROOT))
    print("Saved interpretation:", report.relative_to(ROOT))
    print("RESULT: Billing record structure audit completed; no risk score generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
