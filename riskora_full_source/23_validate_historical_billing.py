"""SLT Step 23: Read-only historical billing data validation.

Run from your SLT_Payment_Project root:
    python 23_validate_historical_billing.py --sample-batches 2
    python 23_validate_historical_billing.py

Only aggregate counts and ranges are saved. No customer/account-level rows
are exported. Input Parquet files are never modified.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "data" / "historical"
REPORT_DIR = ROOT / "outputs" / "private_audit"
EXPECTED_COLUMNS = [
    "product_serial_num", "account_no", "bill_dtm", "payment_due_dtm",
    "balance_fwd_amt", "invoice_net_amt", "outstanding_debt_amt", "payments_amt"
]
AMOUNT_COLUMNS = [
    "balance_fwd_amt", "invoice_net_amt", "outstanding_debt_amt", "payments_amt"
]
DATE_COLUMNS = ["bill_dtm", "payment_due_dtm"]


def blank_stats() -> dict:
    return {
        "processed_rows": 0,
        "missing_account_no": 0,
        "missing_product_serial_num": 0,
        "billing_year_mismatch": 0,
        "due_before_bill": 0,
        "due_more_than_120_days_after_bill": 0,
        "due_more_than_365_days_after_bill": 0,
        "date_fields": {c: {"missing_or_invalid": 0, "minimum": None, "maximum": None}
                        for c in DATE_COLUMNS},
        "amount_fields": {c: {"missing_or_invalid": 0, "negative": 0, "zero": 0,
                               "minimum": None, "maximum": None, "sum": 0.0}
                          for c in AMOUNT_COLUMNS},
        "billing_month_counts": Counter(),
    }


def update_range(stats: dict, minimum, maximum) -> None:
    if minimum is not None:
        stats["minimum"] = minimum if stats["minimum"] is None else min(stats["minimum"], minimum)
    if maximum is not None:
        stats["maximum"] = maximum if stats["maximum"] is None else max(stats["maximum"], maximum)


def process_batch(batch, file_year: int, result: dict, pd) -> None:
    """Summarize one bounded batch in memory; never store raw identifiers."""
    df = batch.to_pandas()
    count = len(df)
    result["processed_rows"] += count

    for column, key in (("account_no", "missing_account_no"),
                        ("product_serial_num", "missing_product_serial_num")):
        values = df[column].astype("string")
        result[key] += int((values.isna() | values.str.strip().fillna("").eq("")).sum())

    dates = {}
    for column in DATE_COLUMNS:
        values = pd.to_datetime(df[column], errors="coerce", utc=True)
        dates[column] = values
        stat = result["date_fields"][column]
        stat["missing_or_invalid"] += int(values.isna().sum())
        good = values.dropna()
        if not good.empty:
            update_range(stat, good.min().date().isoformat(), good.max().date().isoformat())

    bill = dates["bill_dtm"]
    due = dates["payment_due_dtm"]
    result["billing_year_mismatch"] += int((bill.notna() & bill.dt.year.ne(file_year)).sum())
    valid_dates = bill.notna() & due.notna()
    delta_days = (due - bill).dt.total_seconds() / 86400.0
    result["due_before_bill"] += int((valid_dates & delta_days.lt(0)).sum())
    result["due_more_than_120_days_after_bill"] += int((valid_dates & delta_days.gt(120)).sum())
    result["due_more_than_365_days_after_bill"] += int((valid_dates & delta_days.gt(365)).sum())

    months = bill.dt.strftime("%Y-%m").dropna().value_counts()
    for month, n in months.items():
        result["billing_month_counts"][str(month)] += int(n)

    for column in AMOUNT_COLUMNS:
        values = pd.to_numeric(df[column], errors="coerce")
        stats = result["amount_fields"][column]
        stats["missing_or_invalid"] += int(values.isna().sum())
        stats["negative"] += int(values.lt(0).sum())
        stats["zero"] += int(values.eq(0).sum())
        valid = values.dropna()
        if not valid.empty:
            update_range(stats, float(valid.min()), float(valid.max()))
            stats["sum"] += float(valid.sum())


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only validation of historical Parquet billing files")
    parser.add_argument("--sample-batches", type=int, default=0, metavar="N",
                        help="Inspect first N batches of EACH file for a quick test. Omit for full validation.")
    parser.add_argument("--batch-size", type=int, default=65536,
                        help="Rows per processing batch (default: 65536)")
    args = parser.parse_args()
    if args.sample_batches < 0 or args.batch_size < 1000:
        parser.error("--sample-batches must be >= 0 and --batch-size must be >= 1000")

    try:
        import pandas as pd
        import pyarrow.parquet as pq
    except ImportError as exc:
        print("Missing dependency:", exc)
        print("Run: python -m pip install pandas pyarrow")
        return 1

    files = sorted(SOURCE_DIR.glob("DIM_BILL_*_masked.parquet"))
    print("\nSLT STEP 23 — HISTORICAL BILLING VALIDATION")
    print("Source:", SOURCE_DIR)
    print("Mode:", "SAMPLE" if args.sample_batches else "FULL VALIDATION")
    if not files:
        print("ERROR: No files found. Check: Get-ChildItem .\\data\\historical\\*.parquet")
        return 1

    years_found = set()
    expected_schema = None
    failures = []
    results = []
    total_declared_rows = 0
    total_processed_rows = 0

    for path in files:
        match = re.fullmatch(r"DIM_BILL_(\d{4})_masked\.parquet", path.name)
        if not match:
            continue
        year = int(match.group(1))
        years_found.add(year)
        print("\nChecking", path.name, flush=True)
        try:
            parquet = pq.ParquetFile(str(path))
            schema = parquet.schema_arrow
            cols = list(schema.names)
            if not set(EXPECTED_COLUMNS).issubset(cols):
                missing = sorted(set(EXPECTED_COLUMNS) - set(cols))
                raise ValueError("Missing required column(s): " + ", ".join(missing))
            schema_signature = {field.name: str(field.type) for field in schema}
            if expected_schema is None:
                expected_schema = schema_signature
            elif schema_signature != expected_schema:
                failures.append(f"Schema mismatch: {path.name} differs from first file")
            count = int(parquet.metadata.num_rows)
            total_declared_rows += count
            result = blank_stats()
            for i, batch in enumerate(parquet.iter_batches(batch_size=args.batch_size,
                                                             columns=EXPECTED_COLUMNS), 1):
                if args.sample_batches and i > args.sample_batches:
                    break
                process_batch(batch, year, result, pd)
            total_processed_rows += result["processed_rows"]
            result["filename"] = path.name
            result["file_year"] = year
            result["declared_rows"] = count
            result["fully_processed"] = result["processed_rows"] == count
            result["billing_month_counts"] = dict(sorted(result["billing_month_counts"].items()))
            # Round sums only for concise reporting; no accounting reconciliation implied.
            for stat in result["amount_fields"].values():
                stat["sum"] = round(stat["sum"], 2)
            results.append(result)
            print(f"  Declared: {count:,} | Validated: {result['processed_rows']:,}")
            print(f"  Bill dates: {result['date_fields']['bill_dtm']['minimum']} to "
                  f"{result['date_fields']['bill_dtm']['maximum']}")
            print(f"  Due before bill: {result['due_before_bill']:,} | "
                  f"Due >365 days later: {result['due_more_than_365_days_after_bill']:,}")
            print(f"  Billing-year mismatch: {result['billing_year_mismatch']:,}")
        except Exception as exc:
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
            print("  ERROR:", exc)

    years = sorted(years_found)
    gaps = sorted(set(range(min(years), max(years) + 1)) - set(years)) if years else []
    if gaps:
        print("\nNOTICE: Missing year(s) in filename sequence:", ", ".join(map(str, gaps)))
    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(" -", failure)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "sample" if args.sample_batches else "full",
        "sample_batches_per_file": args.sample_batches or None,
        "batch_size": args.batch_size,
        "total_declared_rows": total_declared_rows,
        "total_processed_rows": total_processed_rows,
        "years_in_filenames": years,
        "missing_years": gaps,
        "errors": failures,
        "files": results,
        "interpretation_notes": [
            "Only aggregate results are stored; no raw account identifiers are exported.",
            "A matching column schema does not establish matching masked IDs across years.",
            "Negative monetary values can reflect credits/adjustments; investigate before treating them as errors.",
            "payments_amt is not an individual payment date or proof of on-time settlement.",
            "Current-account state and suspension reasons are not validated by this script.",
            "Exact duplicate invoice detection and cross-year customer linkage are NOT tested here.",
            "Financial sums are diagnostic and do not establish accounting reconciliation.",
        ],
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / "step23_billing_validation.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nReport saved:", target.relative_to(ROOT))
    print(f"Files checked: {len(results)}/{len(files)} | "
          f"Declared rows: {total_declared_rows:,} | Processed rows: {total_processed_rows:,}")
    print("Result:", "CHECK ERRORS" if failures else "VALIDATION COMPLETED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
