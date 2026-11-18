"""SLT Step 27 — aggregate-only financial field diagnostics.

Run from the SLT_Payment_Project root:
  python 27_billing_field_diagnostics.py --sample-rows 20000
  python 27_billing_field_diagnostics.py

Requires: python -m pip install duckdb

READ-ONLY: scans files in data/historical and writes aggregate-only reports
to outputs/private_audit. Does not export account identifiers, calculate
creditworthiness, train a model, or modify the Flask website.

IMPORTANT: The candidate reconciliation below is a *hypothesis to test*,
NOT an assertion about the semantics of masked billing data.
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
REQUIRED = {
    "bill_dtm", "payment_due_dtm", "balance_fwd_amt",
    "invoice_net_amt", "outstanding_debt_amt", "payments_amt"
}

FIELDS = [
    "year", "billing_rows",
    "invoice_negative_rows", "payment_negative_rows",
    "balance_forward_negative_rows", "outstanding_negative_rows",
    "payment_positive_rows", "outstanding_positive_rows",
    "payment_and_outstanding_positive_rows",
    "due_before_bill_rows", "due_over_365_days_rows",
    "candidate_reconciliation_tested_rows", "candidate_reconciliation_near_zero_rows",
    "invoice_null_rows", "payment_null_rows", "outstanding_null_rows",
]

def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect masked billing amount semantics (aggregate-only)")
    parser.add_argument("--sample-rows", type=int, default=0,
                        help="Use first N rows PER FILE as a quick technical check; default 0 scans all rows.")
    parser.add_argument("--memory-limit", default="1GB",
                        help="DuckDB memory limit, for example 1GB or 2048MB.")
    args = parser.parse_args()
    if args.sample_rows < 0:
        parser.error("--sample-rows cannot be negative")
    if not re.fullmatch(r"\d+(MB|GB)", args.memory_limit, re.I):
        parser.error("--memory-limit must be similar to 1GB or 2048MB")
    try:
        import duckdb
    except ImportError:
        print("DuckDB is not installed. Run: python -m pip install duckdb")
        return 1

    paths = sorted(
        ((int(m.group(1)), p) for p in SOURCE.glob("DIM_BILL_*_masked.parquet")
         if (m := re.fullmatch(r"DIM_BILL_(\d{4})_masked\.parquet", p.name))),
        key=lambda pair: pair[0]
    )
    if not paths:
        print("No matching Parquet files found in", SOURCE)
        return 1

    mode = "SAMPLE" if args.sample_rows else "FULL"
    suffix = "sample" if args.sample_rows else "full"
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    con.execute(f"SET memory_limit='{args.memory_limit.upper()}'")
    con.execute("SET threads=2")
    con.execute("SET preserve_insertion_order=false")
    print("\nSLT STEP 27 — BILLING FIELD DIAGNOSTICS")
    print("Mode:", mode, "| Files:", len(paths))
    print("Account IDs are NEVER printed or exported.")
    print("No scoring weights or predicted non-payment outcomes will be generated.")

    results = []
    try:
        for year, p in paths:
            escaped = p.as_posix().replace("'", "''")
            source = f"read_parquet('{escaped}')"
            column_names = {item[0] for item in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
            if not REQUIRED.issubset(column_names):
                missing = sorted(REQUIRED - column_names)
                raise ValueError(f"{p.name}: Missing columns {missing}")
            if args.sample_rows:
                source = f"(SELECT * FROM {source} LIMIT {args.sample_rows})"
            sql = f"""
                WITH bills AS (
                    SELECT
                       TRY_CAST(invoice_net_amt AS DOUBLE) AS inv,
                       TRY_CAST(payments_amt AS DOUBLE) AS pay,
                       TRY_CAST(balance_fwd_amt AS DOUBLE) AS bf,
                       TRY_CAST(outstanding_debt_amt AS DOUBLE) AS debt,
                       TRY_CAST(bill_dtm AS DATE) AS bill_dt,
                       TRY_CAST(payment_due_dtm AS DATE) AS due_dt
                    FROM {source}
                )
                SELECT
                   COUNT(*) AS billing_rows,
                   COUNT(*) FILTER (WHERE inv < 0) AS invoice_negative_rows,
                   COUNT(*) FILTER (WHERE pay < 0) AS payment_negative_rows,
                   COUNT(*) FILTER (WHERE bf < 0) AS balance_forward_negative_rows,
                   COUNT(*) FILTER (WHERE debt < 0) AS outstanding_negative_rows,
                   COUNT(*) FILTER (WHERE pay > 0) AS payment_positive_rows,
                   COUNT(*) FILTER (WHERE debt > 0) AS outstanding_positive_rows,
                   COUNT(*) FILTER (WHERE pay > 0 AND debt > 0) AS payment_and_outstanding_positive_rows,
                   COUNT(*) FILTER (WHERE due_dt < bill_dt) AS due_before_bill_rows,
                   COUNT(*) FILTER (WHERE date_diff('day', bill_dt, due_dt) > 365) AS due_over_365_days_rows,
                   COUNT(*) FILTER (WHERE inv IS NOT NULL AND pay IS NOT NULL
                                            AND bf IS NOT NULL AND debt IS NOT NULL)
                         AS candidate_reconciliation_tested_rows,
                   COUNT(*) FILTER (WHERE inv IS NOT NULL AND pay IS NOT NULL
                                            AND bf IS NOT NULL AND debt IS NOT NULL
                                            AND ABS((bf + inv - pay) - debt) <= 0.01)
                         AS candidate_reconciliation_near_zero_rows,
                   COUNT(*) FILTER (WHERE inv IS NULL) AS invoice_null_rows,
                   COUNT(*) FILTER (WHERE pay IS NULL) AS payment_null_rows,
                   COUNT(*) FILTER (WHERE debt IS NULL) AS outstanding_null_rows
                FROM bills
            """
            cursor = con.execute(sql)
            col_names = [d[0] for d in cursor.description]
            values = cursor.fetchone()
            result = {"year": year, **dict(zip(col_names, [int(v or 0) for v in values]))}
            results.append(result)
            reconciled = result["candidate_reconciliation_near_zero_rows"]
            tested = result["candidate_reconciliation_tested_rows"]
            pct = 100 * reconciled / tested if tested else 0.0
            print(
                f"{p.name}: {result['billing_rows']:,} rows; "
                f"payment>0 {result['payment_positive_rows']:,}; "
                f"debt>0 {result['outstanding_positive_rows']:,}; "
                f"candidate equation match {pct:.1f}%"
            )
    except Exception as exc:
        print("DIAGNOSTIC FAILED:", type(exc).__name__, str(exc))
        return 1
    finally:
        con.close()

    csv_path = OUT / f"step27_billing_field_diagnostics_{suffix}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)

    md_path = OUT / f"step27_billing_field_notes_{suffix}.md"
    lines = [
        "# SLT Step 27 — Billing-field semantics diagnostics",
        "",
        f"Mode: {mode}; generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
        "",
        "**This is an aggregate technical diagnostic, not validated creditworthiness or non-payment prediction.**",
        "",
        "| Year | Rows | Payment > 0 | Outstanding > 0 | Amount-equation match* |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in results:
        tested = r["candidate_reconciliation_tested_rows"]
        pct = 100 * r["candidate_reconciliation_near_zero_rows"] / tested if tested else 0
        lines.append(
            f"| {r['year']} | {r['billing_rows']:,} | {r['payment_positive_rows']:,}"
            f" | {r['outstanding_positive_rows']:,} | {pct:.1f}% |"
        )
    lines.extend([
        "",
        "*Candidate amount equation examined: `balance_fwd_amt + invoice_net_amt - payments_amt ≈ outstanding_debt_amt` (±0.01).",
        "**This is a diagnostic hypothesis only**. A high or low match does not establish the actual business meaning of any field.",
        "The fields may represent snapshots, adjustments, payments in a different period, or different account/product levels.",
        "",
        "## Confirm with SLT data owner before any model",
        "1. Is each row an invoice, product line, monthly account snapshot, or another grain?",
        "2. Does `payments_amt` mean payments toward that invoice, in that month, or cumulative payments?",
        "3. Is `outstanding_debt_amt` an invoice debt or account balance snapshot, and on what as-of date?",
        "4. Does `balance_fwd_amt` refer to the opening balance for the same row/period?",
        "5. Are masked `account_no` values stable across years, and are suspension/settlement labels available?",
        "",
        "## Interpretation safeguards",
        "- A positive outstanding amount on any row is **not** a validated customer default.",
        "- The data does not contain individual payment transaction dates.",
        "- Do not sum snapshot outstanding balances and interpret them as money currently owed.",
        "- Do not create final weights or probabilities from these diagnostics.",
        "- 2025 is intentionally not required for the current pipeline.",
        "",
    ])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print("Saved aggregate CSV:", csv_path.relative_to(ROOT))
    print("Saved explanation:", md_path.relative_to(ROOT))
    print("RESULT: Completed. Await field definitions before validated scoring.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
