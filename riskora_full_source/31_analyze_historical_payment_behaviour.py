"""SLT STEP 31 — Historical payment activity diagnostics (NOT a credit score).

Run from SLT_Payment_Project root (after successful Step 29 and Step 30):
    python 31_analyze_historical_payment_behaviour.py --sample
    python 31_analyze_historical_payment_behaviour.py

Requires DuckDB: python -m pip install duckdb

Reads masked billing Parquet files under data/historical and the Step 29 PRIVATE
account-month DuckDB. Produces aggregate-only diagnostics under
outputs/private_audit. It never prints/exports masked account identifiers and
never modifies the Flask website, original Parquet files or Step 29 database.

Confirmed business descriptions (per supervisor):
    payments_amt = amount paid; 0 means no payment recorded on that row.
    outstanding_debt_amt = amount owed to SLT.

Still UNCONFIRMED: billing-row grain, snapshot vs transaction semantics, payment
posting time, whether payments refer to the current invoice, true settlement
cutoff and longitudinal masking stability. The settlement date bill_settled_dtm
is NOT in the inspected historical Parquet schema. Therefore these indicators
are NOT an unpaid invoice label, default prediction, or validated risk score.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "historical"
OUT = ROOT / "outputs" / "private_audit"
YEARS = (2021, 2022, 2023, 2024, 2026)

RAW_FIELDS = [
    "year", "bill_month", "billing_rows", "positive_payment_rows", "zero_payment_rows",
    "negative_payment_rows", "missing_or_non_numeric_payment_rows", "positive_outstanding_rows",
    "zero_payment_and_positive_outstanding_rows", "positive_payment_and_positive_outstanding_rows",
    "sum_positive_payment_row_amounts", "sum_negative_payment_row_amounts",
]
ACCOUNT_FIELDS = [
    "year", "bill_month", "account_months", "represented_billing_rows",
    "net_positive_payment_account_months", "net_zero_payment_account_months",
    "net_negative_payment_account_months", "positive_outstanding_account_months",
    "net_zero_payment_with_positive_outstanding_account_months",
    "sum_recorded_payment_row_amounts_across_account_months",
]
TRANSITION_FIELDS = [
    "year", "previous_month_net_payment_indicator", "following_month_net_payment_indicator",
    "consecutive_month_account_pairs",
]
YEAR_FIELDS = [
    "year", "billing_rows", "positive_payment_rows", "zero_payment_rows",
    "negative_payment_rows", "missing_or_non_numeric_payment_rows", "positive_outstanding_rows",
    "zero_payment_and_positive_outstanding_rows", "zero_payment_row_share_pct",
    "account_months", "net_positive_payment_account_months", "net_zero_payment_account_months",
    "positive_outstanding_account_months", "consecutive_month_account_pairs",
]


def save_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def source_sql(path: Path, sample: bool, sample_rows: int) -> str:
    quoted = path.as_posix().replace("'", "''")
    scan = f"read_parquet('{quoted}')"
    return f"(SELECT * FROM {scan} LIMIT {sample_rows})" if sample else scan


def fmt(value: int | float) -> str:
    return f"{value:,}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Step 31: aggregate payment activity and within-year monthly behaviour (no risk scores)"
    )
    parser.add_argument("--sample", action="store_true", help="Use Step 29 sample DB and first 20000 source rows per year")
    parser.add_argument("--sample-rows", type=int, default=20000, help="Row limit per source file in sample mode; match Step 29 sample")
    parser.add_argument("--memory-limit", default="1GB", help="DuckDB memory limit, e.g. 1GB or 2GB")
    args = parser.parse_args()
    if args.sample_rows < 1:
        parser.error("--sample-rows must be greater than 0")
    if not re.fullmatch(r"\d+(?:MB|GB)", args.memory_limit.upper()):
        parser.error("--memory-limit must resemble 1GB or 2048MB")

    try:
        import duckdb
    except ImportError:
        print("ERROR: DuckDB is missing. Run: python -m pip install duckdb")
        return 1

    mode = "sample" if args.sample else "full"
    db_path = OUT / f"step29_private_account_month_{mode}.duckdb"
    if not db_path.is_file():
        print(f"ERROR: Step 29 database not found: {db_path}")
        print("Run Step 29 in the same mode first.")
        return 1

    files = {}
    for year in YEARS:
        p = SOURCE / f"DIM_BILL_{year}_masked.parquet"
        if p.is_file():
            files[year] = p
    if not files:
        print(f"ERROR: No historical billing Parquet files found under {SOURCE}")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / f"step31_temp_{mode}"
    tmp.mkdir(parents=True, exist_ok=True)
    print("\nSLT STEP 31 — HISTORICAL PAYMENT BEHAVIOUR DIAGNOSTICS")
    print("Mode:", mode.upper(), "| Years:", ", ".join(map(str, files)))
    print("Amounts are descriptive recorded row values; NOT verified cash collections.")
    print("No customer-level records, identifiers, credit scores or non-payment targets are exported.")
    print("No cross-year account matching; no 2025 dataset required.", flush=True)

    monthly_raw: list[dict] = []
    monthly_accounts: list[dict] = []
    transitions: list[dict] = []
    yearly: list[dict] = []
    conn = None
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        conn.execute(f"SET memory_limit='{args.memory_limit.upper()}'")
        conn.execute("SET threads=2")
        conn.execute("SET preserve_insertion_order=false")
        conn.execute("SET temp_directory='" + tmp.as_posix().replace("'", "''") + "'")

        available = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        for year, file_path in files.items():
            print(f"\nProcessing {file_path.name} ...", flush=True)
            table = f"account_month_{year}"
            if table not in available:
                raise ValueError(f"Missing Step 29 table {table}. Rerun Step 29 in {mode} mode.")

            scan = source_sql(file_path, args.sample, args.sample_rows)
            columns = {r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM {scan}").fetchall()}
            required = {"bill_dtm", "payments_amt", "outstanding_debt_amt"}
            if required - columns:
                raise ValueError(f"{file_path.name} is missing columns: {sorted(required - columns)}")

            # `payments_amt=0` is counted at the SOURCE ROW LEVEL.
            # This is not equivalent to an invoice remaining unpaid after its due date.
            data = conn.execute(f"""
                WITH base AS (
                    SELECT CAST(DATE_TRUNC('month', TRY_CAST(bill_dtm AS DATE)) AS DATE) AS bill_month,
                           TRY_CAST(payments_amt AS DOUBLE) AS p,
                           TRY_CAST(outstanding_debt_amt AS DOUBLE) AS d
                    FROM {scan}
                )
                SELECT bill_month,
                       COUNT(*)::BIGINT AS billing_rows,
                       COUNT(*) FILTER (WHERE p > 0)::BIGINT AS positive_payment_rows,
                       COUNT(*) FILTER (WHERE p = 0)::BIGINT AS zero_payment_rows,
                       COUNT(*) FILTER (WHERE p < 0)::BIGINT AS negative_payment_rows,
                       COUNT(*) FILTER (WHERE p IS NULL)::BIGINT AS missing_payment_rows,
                       COUNT(*) FILTER (WHERE d > 0)::BIGINT AS positive_debt_rows,
                       COUNT(*) FILTER (WHERE p = 0 AND d > 0)::BIGINT AS zero_payment_and_debt_rows,
                       COUNT(*) FILTER (WHERE p > 0 AND d > 0)::BIGINT AS positive_payment_and_debt_rows,
                       COALESCE(SUM(p) FILTER (WHERE p > 0), 0)::DOUBLE AS positive_payment_sum,
                       COALESCE(SUM(p) FILTER (WHERE p < 0), 0)::DOUBLE AS negative_payment_sum
                FROM base
                GROUP BY bill_month
                ORDER BY bill_month NULLS LAST
            """).fetchall()
            raw_year = defaultdict(int)
            for r in data:
                row = dict(zip(RAW_FIELDS[1:], [str(r[0]) if r[0] is not None else "", *r[1:]]))
                row = {"year": year, **row}
                monthly_raw.append(row)
                for k in RAW_FIELDS[2:10]:
                    raw_year[k] += int(row[k])

            # Step 29 contains account-month totals, not individual invoice settlements.
            # An account-month sum of zero can contain offsetting/missing amounts;
            # it must not be labelled 'customer did not pay'.
            account_data = conn.execute(f"""
                SELECT bill_month,
                       COUNT(*)::BIGINT AS account_months,
                       COALESCE(SUM(billing_record_count),0)::BIGINT AS billing_rows,
                       COUNT(*) FILTER (WHERE payment_row_amount_sum_UNVERIFIED > 0)::BIGINT AS net_positive,
                       COUNT(*) FILTER (WHERE payment_row_amount_sum_UNVERIFIED = 0)::BIGINT AS net_zero,
                       COUNT(*) FILTER (WHERE payment_row_amount_sum_UNVERIFIED < 0)::BIGINT AS net_negative,
                       COUNT(*) FILTER (WHERE positive_debt_row_count > 0)::BIGINT AS has_debt,
                       COUNT(*) FILTER (WHERE payment_row_amount_sum_UNVERIFIED = 0
                                          AND positive_debt_row_count > 0)::BIGINT AS zero_and_debt,
                       COALESCE(SUM(payment_row_amount_sum_UNVERIFIED),0)::DOUBLE AS payment_amount_sum
                FROM {table}
                GROUP BY bill_month
                ORDER BY bill_month
            """).fetchall()
            acct_year = defaultdict(int)
            for r in account_data:
                row = dict(zip(ACCOUNT_FIELDS[1:], [str(r[0]), *r[1:]]))
                row = {"year": year, **row}
                monthly_accounts.append(row)
                for k in ACCOUNT_FIELDS[2:9]:
                    acct_year[k] += int(row[k])

            # Only pairs of adjacent calendar months within the SAME year.
            # This measures recorded payment-amount activity, not future non-payment.
            pair_data = conn.execute(f"""
                SELECT
                    CASE WHEN a.payment_row_amount_sum_UNVERIFIED > 0 THEN 'positive'
                         WHEN a.payment_row_amount_sum_UNVERIFIED < 0 THEN 'negative'
                         ELSE 'zero_or_missing' END AS previous_month_indicator,
                    CASE WHEN b.payment_row_amount_sum_UNVERIFIED > 0 THEN 'positive'
                         WHEN b.payment_row_amount_sum_UNVERIFIED < 0 THEN 'negative'
                         ELSE 'zero_or_missing' END AS following_month_indicator,
                    COUNT(*)::BIGINT AS pair_count
                FROM {table} a
                JOIN {table} b
                  ON a.masked_account_id = b.masked_account_id
                 AND b.bill_month = CAST(a.bill_month + INTERVAL '1 month' AS DATE)
                GROUP BY 1, 2
                ORDER BY 1, 2
            """).fetchall()
            total_pairs = 0
            for prev, nxt, n in pair_data:
                transitions.append({
                    "year": year,
                    "previous_month_net_payment_indicator": prev,
                    "following_month_net_payment_indicator": nxt,
                    "consecutive_month_account_pairs": int(n),
                })
                total_pairs += int(n)

            billing_rows = raw_year["billing_rows"]
            yearly_row = {
                "year": year,
                "billing_rows": billing_rows,
                "positive_payment_rows": raw_year["positive_payment_rows"],
                "zero_payment_rows": raw_year["zero_payment_rows"],
                "negative_payment_rows": raw_year["negative_payment_rows"],
                "missing_or_non_numeric_payment_rows": raw_year["missing_or_non_numeric_payment_rows"],
                "positive_outstanding_rows": raw_year["positive_outstanding_rows"],
                "zero_payment_and_positive_outstanding_rows": raw_year["zero_payment_and_positive_outstanding_rows"],
                "zero_payment_row_share_pct": round(100 * raw_year["zero_payment_rows"] / billing_rows, 3) if billing_rows else None,
                "account_months": acct_year["account_months"],
                "net_positive_payment_account_months": acct_year["net_positive_payment_account_months"],
                "net_zero_payment_account_months": acct_year["net_zero_payment_account_months"],
                "positive_outstanding_account_months": acct_year["positive_outstanding_account_months"],
                "consecutive_month_account_pairs": total_pairs,
            }
            yearly.append(yearly_row)
            print("  Source billing records:", fmt(billing_rows))
            print("  Payment value = 0 source rows:", fmt(yearly_row["zero_payment_rows"]))
            print("  Payment value > 0 source rows:", fmt(yearly_row["positive_payment_rows"]))
            print("  Positive outstanding source rows:", fmt(yearly_row["positive_outstanding_rows"]))
            print("  Account-months with net-zero payment row sums:", fmt(yearly_row["net_zero_payment_account_months"]))
            print("  Consecutive-month account pairs:", fmt(total_pairs), flush=True)

        tag = mode
        files_out = [
            (f"step31_yearly_payment_diagnostics_{tag}.csv", YEAR_FIELDS, yearly),
            (f"step31_monthly_payment_row_diagnostics_{tag}.csv", RAW_FIELDS, monthly_raw),
            (f"step31_account_month_payment_patterns_{tag}.csv", ACCOUNT_FIELDS, monthly_accounts),
            (f"step31_monthly_payment_transitions_{tag}.csv", TRANSITION_FIELDS, transitions),
        ]
        for fname, headers, rows in files_out:
            save_csv(OUT / fname, headers, rows)
            print("Saved aggregate CSV:", (OUT / fname).relative_to(ROOT))

        notes_path = OUT / f"step31_payment_behaviour_notes_{tag}.md"
        notes = [
            "# Step 31 — Historical payment activity diagnostics",
            "",
            f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}",
            f"Mode: {tag}",
            "",
            "## Confirmed field meanings (from supervisor)",
            "- `payments_amt` represents an amount paid; `payments_amt = 0` is a no-payment value on that particular billing record.",
            "- `outstanding_debt_amt` represents the amount the customer owes SLT.",
            "",
            "## What the files actually measure",
            "- `step31_monthly_payment_row_diagnostics`: counts of raw billing rows with positive / zero / negative / missing payments, grouped by BILL month.",
            "- `step31_account_month_payment_patterns`: account-month groupings from Step 29. `net_zero_payment_account_months` means Step 29 SUM(payments_amt)=0, NOT a verified no-payment customer.",
            "- `step31_monthly_payment_transitions`: changes in NET account-month payment amounts among customers seen in two adjacent months in the same year.",
            "- `step31_yearly_payment_diagnostics`: annual aggregation of the same descriptive measures.",
            "",
            "## Important limitations",
            "- No actual payment-transaction date or bill settlement date is available in the inspected historical files; do not calculate payment delay from `bill_dtm`.",
            "- `bill_settled_dtm` was mentioned by the supervisor but has not been found in the five inspected Parquet files. Its source and join keys remain to be confirmed.",
            "- A billing row with `payments_amt = 0` must NOT be labelled an unpaid invoice or a default; due date, snapshot date and subsequent settlement matter.",
            "- The amount fields can refer to snapshots or billing-row measures. Amount sums are DESCRIPTIVE and not confirmed unique revenue, recovered cash or final exposure.",
            "- An account-month net zero can result from no payment, missing values treated as zero by Step 29, or offsetting positive and negative values.",
            "- Historical account IDs are linked only inside one calendar year; stable masking across years has not been established.",
            "- Months without a billing row are not treated as zero payment months.",
            "- 2026 is a partial year; 2025 was intentionally not required.",
            "- In sample mode, only the first N source rows per file are scanned; this is a smoke test, not a representative sample.",
            "- All generated files belong under outputs/private_audit. Do not commit them or masked raw data to public GitHub.",
            "",
            "## Next step",
            "Confirm the location/join keys of `bill_settled_dtm` or obtain a time-stamped balance/payment source, then specify a month-end observation time and one-month-later verified payment outcome before learning statistical score weights.",
        ]
        notes_path.write_text("\n".join(notes) + "\n", encoding="utf-8")
        print("Saved interpretation:", notes_path.relative_to(ROOT))
        print("RESULT: Step 31 completed. Descriptive payment diagnostics only; NO scoring weights or non-payment labels created.")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        print("Original Parquet files, Step 29 database and Flask website were not modified.")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
