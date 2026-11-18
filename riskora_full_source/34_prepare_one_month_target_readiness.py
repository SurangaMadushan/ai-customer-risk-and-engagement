"""SLT STEP 34 — One-month-ahead non-payment TARGET READINESS (no labels).

Run in the main SLT_Payment_Project directory:
    python 34_prepare_one_month_target_readiness.py --sample
    python 34_prepare_one_month_target_readiness.py

Optional:
    python 34_prepare_one_month_target_readiness.py --memory-limit 2GB

Data: data/historical/DIM_BILL_YYYY_masked.parquet (2021, 2022, 2023, 2024, 2026).
Optional: reads Step 33 readiness JSON when present.

Outputs (aggregate/private only, under outputs/private_audit):
    step34_due_month_eligibility_{sample|full}.csv
    step34_source_field_audit_{sample|full}.csv
    step34_target_readiness_{sample|full}.json
    step34_prediction_target_contract_{sample|full}.md

IMPORTANT:
- Does NOT create non-payment labels, score weights, model probabilities, or train ML.
- A zero payments_amt row is NOT proof of invoice non-payment.
- A positive outstanding_debt_amt row is NOT a validated as-of debt outcome.
- bill_settled_dtm and/or timestamped payment events must be located/validated.
- No customer IDs or row-level data are printed or exported.
- Existing Flask site, source Parquet and private feature DBs are unchanged.
- No cross-year masked-ID joins. 2025 billing is NOT required.
- Keep private reports and all SLT datasets OUT of public GitHub.
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
YEARS = (2021, 2022, 2023, 2024, 2026)
ESSENTIAL = {"bill_dtm", "payment_due_dtm", "payments_amt", "outstanding_debt_amt"}

AUDIT_COLUMNS = [
    "source_year", "file_found", "rows_in_source_metadata", "bill_dtm_present",
    "payment_due_dtm_present", "payments_amt_present", "outstanding_debt_amt_present",
    "bill_settled_dtm_present", "invoice_level_id_present",
    "dated_payment_event_present", "account_no_present", "product_serial_num_present",
    "candidate_non_payment_target_ready", "comment",
]
DUE_COLUMNS = [
    "source_year", "due_month", "rows_due_in_month",
    "rows_with_bill_date_before_due_month", "rows_with_bill_date_in_or_after_due_month",
    "rows_with_zero_recorded_payment_AMBIGUOUS",
    "rows_with_positive_outstanding_AMBIGUOUS",
    "rows_with_invalid_due_before_bill", "rows_with_due_gap_over_365_days",
    "rows_with_invalid_or_missing_bill_date",
]


def csv_write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sq(path: Path) -> str:
    return str(path.as_posix()).replace("'", "''")


def metadata_audit(con, year: int, path: Path) -> dict:
    result = {"source_year": year, "file_found": path.is_file(),
              "rows_in_source_metadata": 0}
    if not path.is_file():
        return {
            **result,
            **{field: False for field in AUDIT_COLUMNS if field.endswith("_present")},
            "candidate_non_payment_target_ready": False,
            "comment": "Source Parquet not found",
        }
    # DESCRIBE/metadata does not load individual account records.
    cols = {str(row[0]).lower() for row in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{sq(path)}')"
    ).fetchall()}
    # DuckDB's parquet_metadata exposes row_group_num_rows, which is metadata
    # only and avoids reading individual rows.
    metadata_count = con.execute(
        f"SELECT SUM(row_group_num_rows)::BIGINT FROM parquet_metadata('{sq(path)}')"
    ).fetchone()[0]
    result["rows_in_source_metadata"] = int(metadata_count or 0)
    payment_event_dates = ("payment_dtm", "payment_date", "payment_txn_dtm",
                           "payment_posted_dtm", "payment_received_dtm")
    invoice_id_names = ("invoice_id", "invoice_no", "bill_id", "bill_no")
    result.update({
        "bill_dtm_present": "bill_dtm" in cols,
        "payment_due_dtm_present": "payment_due_dtm" in cols,
        "payments_amt_present": "payments_amt" in cols,
        "outstanding_debt_amt_present": "outstanding_debt_amt" in cols,
        "bill_settled_dtm_present": "bill_settled_dtm" in cols,
        "invoice_level_id_present": any(c in cols for c in invoice_id_names),
        "dated_payment_event_present": any(c in cols for c in payment_event_dates),
        "account_no_present": "account_no" in cols,
        "product_serial_num_present": "product_serial_num" in cols,
    })
    result["candidate_non_payment_target_ready"] = False
    result["comment"] = (
        "Schema alone cannot confirm point-in-time as-of status, unique invoice keys, "
        "settlement meaning or validated labels"
    )
    return result


def due_month_summary(con, year: int, path: Path, sample_rows: int) -> list[dict]:
    source_expr = f"read_parquet('{sq(path)}')"
    if sample_rows:
        source_expr = f"(SELECT * FROM {source_expr} LIMIT {sample_rows})"
    # Whole-month due windows. This is a descriptive eligibility audit only;
    # observed amounts may reflect retrospectively updated fields.
    sql = f"""
    WITH source_rows AS (
      SELECT TRY_CAST(bill_dtm AS DATE) AS bill_date,
             TRY_CAST(payment_due_dtm AS DATE) AS due_date,
             TRY_CAST(payments_amt AS DOUBLE) AS paid_value,
             TRY_CAST(outstanding_debt_amt AS DOUBLE) AS debt_value
      FROM {source_expr}
    ), d AS (
      SELECT bill_date, due_date, paid_value, debt_value,
             CAST(DATE_TRUNC('month', due_date) AS DATE) AS due_month,
             DATE_DIFF('day', bill_date, due_date) AS due_gap_days
      FROM source_rows
    )
    SELECT due_month,
           COUNT(*)::BIGINT AS n,
           COUNT(*) FILTER (
             WHERE bill_date < due_month
           )::BIGINT AS billed_before,
           COUNT(*) FILTER (
             WHERE bill_date >= due_month
           )::BIGINT AS billed_during_or_after,
           COUNT(*) FILTER (WHERE paid_value = 0)::BIGINT AS zero_pay,
           COUNT(*) FILTER (WHERE debt_value > 0)::BIGINT AS pos_debt,
           COUNT(*) FILTER (WHERE due_gap_days < 0)::BIGINT AS due_before,
           COUNT(*) FILTER (WHERE due_gap_days > 365)::BIGINT AS due_over365,
           COUNT(*) FILTER (WHERE bill_date IS NULL)::BIGINT AS invalid_bill
    FROM d
    WHERE due_month IS NOT NULL
    GROUP BY 1
    ORDER BY 1
    """
    rows = []
    for (month, n, before, after, no_pay, debt, bad_gap, long_gap, bad_bill) in con.execute(sql).fetchall():
        rows.append({
            "source_year": year,
            "due_month": str(month),
            "rows_due_in_month": int(n),
            "rows_with_bill_date_before_due_month": int(before),
            "rows_with_bill_date_in_or_after_due_month": int(after),
            "rows_with_zero_recorded_payment_AMBIGUOUS": int(no_pay),
            "rows_with_positive_outstanding_AMBIGUOUS": int(debt),
            "rows_with_invalid_due_before_bill": int(bad_gap),
            "rows_with_due_gap_over_365_days": int(long_gap),
            "rows_with_invalid_or_missing_bill_date": int(bad_bill),
        })
    return rows


def maybe_step33(mode: str) -> dict:
    path = OUT / f"step33_model_readiness_{mode}.json"
    if not path.is_file():
        return {"status": "not_found", "filename": path.name}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return {"status": "loaded", "filename": path.name,
                "readiness": doc.get("readiness", "not provided"),
                "quality_check_fail_count_may_overlap": doc.get(
                    "quality_check_fail_count_may_overlap")}
    except (OSError, ValueError) as exc:
        return {"status": "unreadable", "filename": path.name,
                "error_type": type(exc).__name__}


def report(mode: str, source_audit: list[dict], due_rows: list[dict],
           prior_step: dict, generated: str) -> str:
    total_due = sum(r["rows_due_in_month"] for r in due_rows)
    billed_after_cutoff = sum(r["rows_with_bill_date_in_or_after_due_month"] for r in due_rows)
    billed_before = sum(r["rows_with_bill_date_before_due_month"] for r in due_rows)
    missing_source = [str(r["source_year"]) for r in source_audit if not r["file_found"]]
    missing_settlement = [str(r["source_year"]) for r in source_audit
                          if not r["bill_settled_dtm_present"]]
    return "\n".join([
        "# SLT Step 34 — One-month non-payment target readiness",
        "", f"Generated UTC: {generated}", f"Mode: **{mode.upper()}**",
        "", "**STATUS: HOLD — no confirmed non-payment labels can be generated from the supplied Parquet fields alone.**",
        "", "## Madam's agreed horizon",
        "- One-month-ahead prediction; this is a FORECAST HORIZON, not proof of invoice-level default.",
        "- **Proposed target (requires business sign-off):** at the end of month T, predict whether an active customer account will have an eligible bill due in month T+1 that remains unpaid at end of T+1.",
        "- Alternative (different question): at invoice issue time, predict whether an invoice remains unpaid 30 days AFTER its due date. Do not mix these labels.",
        "- The precise eligibility, grace period, partial-payment threshold, open/closed service rules and holiday policy require confirmation.",
        "", "## What the data currently supports",
        f"- Source files not found: {', '.join(missing_source) if missing_source else 'none'}.",
        f"- Source years missing `bill_settled_dtm`: {', '.join(missing_settlement)}.",
        f"- Billing rows having a parseable due month within this {mode} audit: **{total_due:,}**.",
        f"- Rows billed BEFORE their due month: **{billed_before:,}** (potentially already issued by previous-month end).",
        f"- Rows billed IN or AFTER their due month: **{billed_after_cutoff:,}** (invoice details were not necessarily available at previous-month-end prediction time).",
        "- These counts refer to BILLING ROWS, not distinct customers or verified invoice outcomes.",
        "- The values of `payments_amt` and `outstanding_debt_amt` are descriptive snapshots with unconfirmed historical as-of behaviour. Do NOT turn zero payments or positive debt into supervised target labels.",
        "- Repeated account+product+bill-date keys exist (Step 28); the row-to-invoice identifier is unconfirmed.",
        "- Input files may contain due months outside their filename year; a due month is not the same as a billing month.",
        "- No cross-year account matching is performed; 2025 remains optional and 2026 is a partial year.",
        "", "## Mandatory data contract before modelling",
        "1. Source/file/table containing **`bill_settled_dtm`**, or a dated payment transaction ledger.",
        "2. A trustworthy invoice or bill identifier and mapping to the billing rows, including repeated keys.",
        "3. As-of / extraction timestamps for amount fields (were payments/outstanding updated after the prediction or evaluation cutoff?).",
        "4. Agreed target cutoff: one-month customer forecast vs invoice +30-day target, partial payments, eligibility and grace policy.",
        "5. Evidence that mask/token mapping is stable where historical records are linked, especially across years.",
        "6. Production approval: access controls, data residency, human review and permissible collection actions.",
        "", "## After source contract is confirmed",
        "- Build label creation with settlement dates or timestamped invoice-linked payments and historical as-of balances.",
        "- Anchor each customer's historical features strictly BEFORE its forecast cutoff.",
        "- Train on earlier calendar periods; evaluate on later periods without leakage; calibrate probabilities.",
        "- Explain importance/coefficient effects and derive a documented, validated scoring method with SLT approval.",
        "- Until then, keep Flask's current score labelled **PROVISIONAL / NOT STATISTICALLY VALIDATED**.",
        "", "## Step 33 check",
        f"- Prior report: {prior_step.get('status')} ({prior_step.get('filename', 'unknown')}).",
        "", "## Privacy",
        "- All reports contain only aggregate counts, field names and methodology notes.",
        "- Never commit `outputs/private_audit` or real datasets to public GitHub.",
        "- Sample mode is FIRST 20,000 rows per source year; not a random or representative sample.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 34 — audit one-month non-payment target readiness")
    parser.add_argument("--sample", action="store_true", help="Inspect first 20,000 billing rows PER source")
    parser.add_argument("--memory-limit", default="1GB", help="DuckDB memory limit, e.g. 1GB or 2048MB")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+(?:MB|GB)", args.memory_limit.upper()):
        parser.error("--memory-limit should be like 1GB or 2048MB")
    try:
        import duckdb
    except ImportError:
        print("ERROR: DuckDB missing; install with: python -m pip install duckdb")
        return 1
    mode = "sample" if args.sample else "full"
    OUT.mkdir(parents=True, exist_ok=True)
    temp_dir = OUT / f"step34_tmp_{mode}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    print("\nSLT STEP 34 — ONE-MONTH NON-PAYMENT TARGET READINESS")
    print("Mode:", mode.upper(), "| Sources:", len(YEARS))
    print("No account identifiers, target labels, model training or score weights will be generated.")
    con = None
    try:
        con = duckdb.connect(database=":memory:")
        con.execute(f"SET memory_limit='{args.memory_limit.upper()}'")
        con.execute("SET threads=2")
        con.execute("SET preserve_insertion_order=false")
        con.execute(f"SET temp_directory='{sq(temp_dir)}'")
        audits, due_rows = [], []
        for year in YEARS:
            path = SOURCE / f"DIM_BILL_{year}_masked.parquet"
            print(f"\nChecking {path.name} ...", flush=True)
            if not path.is_file():
                print("  File not found (source skipped)")
                audits.append(metadata_audit(con, year, path))
                continue
            try:
                one = metadata_audit(con, year, path)
            except Exception as exc:
                # Metadata syntax differs across DuckDB releases; fallback to
                # schema-only audit rather than giving false field readings.
                if "parquet_metadata" not in str(exc).lower() and "row_group_num_rows" not in str(exc).lower():
                    raise
                src = f"read_parquet('{sq(path)}')"
                cols = {str(r[0]).lower() for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
                one = {
                    "source_year": year, "file_found": True,
                    "rows_in_source_metadata": "unknown (older DuckDB)",
                    "bill_dtm_present": "bill_dtm" in cols,
                    "payment_due_dtm_present": "payment_due_dtm" in cols,
                    "payments_amt_present": "payments_amt" in cols,
                    "outstanding_debt_amt_present": "outstanding_debt_amt" in cols,
                    "bill_settled_dtm_present": "bill_settled_dtm" in cols,
                    "invoice_level_id_present": any(s in cols for s in ("invoice_id", "invoice_no", "bill_id", "bill_no")),
                    "dated_payment_event_present": any(s in cols for s in ("payment_dtm", "payment_date", "payment_txn_dtm", "payment_posted_dtm")),
                    "account_no_present": "account_no" in cols,
                    "product_serial_num_present": "product_serial_num" in cols,
                    "candidate_non_payment_target_ready": False,
                    "comment": "Source row total unavailable from parquet_metadata; target blocked regardless",
                }
            audits.append(one)
            available = {
                "bill_dtm": one["bill_dtm_present"],
                "payment_due_dtm": one["payment_due_dtm_present"],
                "payments_amt": one["payments_amt_present"],
                "outstanding_debt_amt": one["outstanding_debt_amt_present"],
            }
            absent = sorted(k for k, v in available.items() if not v)
            if absent:
                print("  Missing essential columns:", ", ".join(absent))
                continue
            per_month = due_month_summary(con, year, path, 20000 if args.sample else 0)
            due_rows.extend(per_month)
            count = sum(r["rows_due_in_month"] for r in per_month)
            print(f"  Rows assigned to a valid due month: {count:,}")
            print(f"  Due-month groups: {len(per_month)}")
            print("  bill_settled_dtm present:", one["bill_settled_dtm_present"])

        prior = maybe_step33(mode)
        generated = datetime.now(timezone.utc).isoformat()
        paths = [
            OUT / f"step34_due_month_eligibility_{mode}.csv",
            OUT / f"step34_source_field_audit_{mode}.csv",
            OUT / f"step34_target_readiness_{mode}.json",
            OUT / f"step34_prediction_target_contract_{mode}.md",
        ]
        csv_write(paths[0], DUE_COLUMNS, due_rows)
        csv_write(paths[1], AUDIT_COLUMNS, audits)
        payload = {
            "generated_utc": generated,
            "mode": mode,
            "years_requested": list(YEARS),
            "decision": "HOLD: no validated future non-payment labels from these fields alone",
            "rows_with_valid_due_month": sum(r["rows_due_in_month"] for r in due_rows),
            "billing_rows_not_necessarily_known_at_prior_month_cutoff": sum(
                r["rows_with_bill_date_in_or_after_due_month"] for r in due_rows
            ),
            "sources": audits,
            "prior_step33_summary": prior,
            "not_created": ["customer labels", "scores", "weights", "ML models", "predictions"],
        }
        paths[2].write_text(json.dumps(payload, indent=2), encoding="utf-8")
        paths[3].write_text(report(mode, audits, due_rows, prior, generated), encoding="utf-8")
        print("\nSaved:")
        for p in paths:
            print(" ", p.relative_to(ROOT))
        print("RESULT: Target readiness audited. No default labels or credit scores generated.")
        print("NEXT: Verify bill_settled_dtm source, invoice mapping and historical as-of payment values.")
        return 0
    except Exception as exc:
        print("ERROR:", type(exc).__name__ + ":", exc)
        print("Raw Parquet, Step 29–33 databases and Flask website remain unchanged.")
        return 1
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
