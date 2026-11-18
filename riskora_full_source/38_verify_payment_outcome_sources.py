#!/usr/bin/env python3
"""SLT Step 38 — Locate reliable settlement/payment-outcome sources.

This is the next GATE before training a statistically validated creditworthiness
model. Inspect schemas/Parquet metadata ONLY (no customer-level reads or export).

From your SLT_Payment_Project folder:
    python 38_verify_payment_outcome_sources.py

To inspect a NEW payment/settlement file (after SLT authorization):
    python 38_verify_payment_outcome_sources.py --extra-file "D:\\Data\\settlements.parquet"

To scan another folder containing approved files (non-recursive):
    python 38_verify_payment_outcome_sources.py --scan-dir "D:\\Data"

Run after Steps 22–37. No 2025 file, usage data, or Step 37 rerun required.

Reports saved privately to outputs/private_audit/step38_*. Do NOT publish
these reports or any SLT datasets on public GitHub. No scores/labels trained.

Why this is necessary:
  * Earlier historical Parquet files had bill/due dates, payments_amt, debt
    but neither bill_settled_dtm nor an invoice-level identifier.
  * Repeated account+product+bill-date keys exist: this composite is NOT a
    guaranteed unique invoice key.
  * Retrospectively updated amounts cannot safely label past as-of outcomes.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORICAL = ROOT / "data" / "historical"
PRIVATE = ROOT / "outputs" / "private_audit"
SUPPORTED_EXTENSIONS = {".parquet", ".csv"}
BILL_FIELDS = {"bill_dtm", "bill_date", "invoice_date", "billing_date"}
DUE_FIELDS = {"payment_due_dtm", "due_date", "payment_due_date", "invoice_due_date"}
SETTLEMENT_FIELDS = {
    "bill_settled_dtm", "bill_settled_date", "settlement_dtm",
    "settlement_date", "settled_dtm", "settled_date", "invoice_settled_at",
}
PAYMENT_DATE_FIELDS = {
    "payment_dtm", "payment_date", "payment_txn_dtm", "payment_received_dtm",
    "payment_posted_dtm", "receipt_dtm", "transaction_date",
}
PAYMENT_AMOUNT_FIELDS = {"payments_amt", "payment_amt", "payment_amount", "amount_paid"}
INVOICE_KEY_FIELDS = {
    "invoice_id", "invoice_no", "invoice_number", "bill_id", "bill_no",
    "bill_number", "invoice_key",
}
ACCOUNT_KEY_FIELDS = {"account_no", "account_id", "customer_id", "masked_account_id"}
PRODUCT_KEY_FIELDS = {"product_serial_num", "service_id", "product_id"}
SNAPSHOT_FIELDS = {
    "as_of_date", "as_of_dtm", "snapshot_date", "snapshot_dtm", "extract_dtm",
    "effective_dtm", "effective_date", "record_updated_dtm",
}
BALANCE_FIELDS = {"outstanding_debt_amt", "unpaid_balance", "outstanding_amt", "balance_due"}
COLUMNS = [
    "file", "kind", "row_count_from_metadata", "column_count", "bill_date_fields",
    "due_date_fields", "settlement_date_fields", "payment_event_date_fields",
    "payment_amount_fields", "invoice_key_fields", "account_key_fields",
    "product_key_fields", "snapshot_date_fields", "balance_fields", "notes",
]


def matched(columns: set[str], candidates: set[str]) -> str:
    return ", ".join(sorted(columns & candidates))


def read_schema(path: Path) -> tuple[list[str], int | None, str]:
    """Read schema without materializing rows (CSV header; Parquet metadata)."""
    if path.suffix.lower() == ".csv":
        for encoding in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                # CSV reader obtains just first header record, not rows.
                with path.open("r", encoding=encoding, newline="") as stream:
                    header = next(csv.reader(stream), [])
                if len(header) == 1 and "," in header[0]:
                    continue
                return [str(x).strip() for x in header], None, "CSV header only; row count not scanned"
            except (UnicodeError, csv.Error):
                continue
        raise ValueError("CSV header could not be decoded")
    if path.suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("pyarrow is missing. Run: python -m pip install pyarrow") from exc
        pf = pq.ParquetFile(str(path))
        return pf.schema_arrow.names, pf.metadata.num_rows, "Parquet metadata only"
    raise ValueError("Unsupported file extension: " + path.suffix)


def collect_files(args: argparse.Namespace) -> tuple[list[Path], list[str]]:
    messages: list[str] = []
    paths: dict[str, Path] = {}
    if HISTORICAL.is_dir():
        for p in sorted(HISTORICAL.iterdir()):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                paths[str(p.resolve()).casefold()] = p
    else:
        messages.append("Default data/historical folder not found on this machine.")
    for p in args.extra_file:
        p = p.expanduser()
        if not p.is_file():
            messages.append("Additional file not found: " + p.name)
        elif p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            messages.append("Unsupported additional format: " + p.name)
        else:
            paths[str(p.resolve()).casefold()] = p
    if args.scan_dir:
        folder = args.scan_dir.expanduser()
        if not folder.is_dir():
            messages.append("Additional scan folder not found: " + str(folder))
        else:
            choices = [p for p in sorted(folder.iterdir())
                       if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
            if len(choices) > 200:
                messages.append("Scan folder has more than 200 supported files; inspected first 200 only.")
            for p in choices[:200]:
                paths[str(p.resolve()).casefold()] = p
    return list(paths.values()), messages


def one_file(path: Path) -> dict:
    fields, row_count, note = read_schema(path)
    # Preserve local source column names as evidence in private report only.
    cols = {str(x).strip().casefold() for x in fields}
    return {
        "file": path.name,
        "kind": path.suffix.lower().removeprefix("."),
        "row_count_from_metadata": row_count if row_count is not None else "",
        "column_count": len(fields),
        "bill_date_fields": matched(cols, BILL_FIELDS),
        "due_date_fields": matched(cols, DUE_FIELDS),
        "settlement_date_fields": matched(cols, SETTLEMENT_FIELDS),
        "payment_event_date_fields": matched(cols, PAYMENT_DATE_FIELDS),
        "payment_amount_fields": matched(cols, PAYMENT_AMOUNT_FIELDS),
        "invoice_key_fields": matched(cols, INVOICE_KEY_FIELDS),
        "account_key_fields": matched(cols, ACCOUNT_KEY_FIELDS),
        "product_key_fields": matched(cols, PRODUCT_KEY_FIELDS),
        "snapshot_date_fields": matched(cols, SNAPSHOT_FIELDS),
        "balance_fields": matched(cols, BALANCE_FIELDS),
        "notes": note,
    }


def source_state(rows: list[dict]) -> dict:
    dated = [r["file"] for r in rows if r["settlement_date_fields"] or r["payment_event_date_fields"]]
    invoice = [r["file"] for r in rows if r["invoice_key_fields"]]
    dates_and_key = [r["file"] for r in rows if r["invoice_key_fields"] and
                     (r["settlement_date_fields"] or r["payment_event_date_fields"])]
    snapshots = [r["file"] for r in rows if r["snapshot_date_fields"]]
    return {
        "timestamp_candidates_found_in": dated,
        "invoice_id_candidates_found_in": invoice,
        "timestamp_and_invoice_id_in_same_file": dates_and_key,
        "snapshot_timestamp_candidates_found_in": snapshots,
        # Schema alone cannot certify linkage, data semantics or target quality.
        "real_one_month_target_validated": False,
        "creditworthiness_weights_predictively_validated": False,
        "model_ready_for_production": False,
    }


def make_contract(rows: list[dict], state: dict, errors: list[str], timestamp: str) -> str:
    lines = [
        "# SLT Step 38 — Verified payment-outcome source discovery",
        "", f"Generated UTC: {timestamp}",
        "", "## Decision",
        "**HOLD — Actual one-month non-payment outcomes are not validated.**",
        "", "This audit checks only file headers / Parquet metadata. It does NOT train a model,",
        "compute scores, create default labels, or prove that payment history is point-in-time accurate.",
        "", "## Data inventory (counts are Parquet metadata only)",
        "", "| Source file | Rows (if known) | Settlement date | Payment event date | Invoice key | Snapshot timestamp |",
        "|---|---:|---|---|---|---|",
    ]
    for r in rows:
        def cell(key: str) -> str:
            return str(r[key] or "Not present").replace("|", "/")
        lines.append(f"| {r['file']} | {r['row_count_from_metadata'] or 'Not scanned'} | "
                     f"{cell('settlement_date_fields')} | {cell('payment_event_date_fields')} | "
                     f"{cell('invoice_key_fields')} | {cell('snapshot_date_fields')} |")
    if not rows:
        lines.append("| No supported CSV/Parquet files located | — | — | — | — | — |")
    lines += [
        "", "## One-month-ahead target proposal — require SLT business sign-off",
        "- At end of calendar month **T**, predict whether an eligible account will have a bill",
        "  **due during month T+1** that remains **not fully settled by the end of T+1**.",
        "- Define eligibility (active accounts, invoice cancellations, credits, disputes),",
        "  minimum unpaid balance, grace periods, and whether partial payments count as unpaid.",
        "- This **customer-level next-month** target is different from the older",
        "  **invoice unpaid 30 days AFTER due date** target; do not mix their metrics.",
        "- A zero `payments_amt` row means no payment recorded for that row, not necessarily",
        "  that the bill was eligible and unpaid on a particular historical cutoff.",
        "- `outstanding_debt_amt` represents amount owed, but its effective date and",
        "  record grain must be confirmed before making as-of labels.",
        "", "## Technical data contract (required before predictive weight validation)",
        "1. Invoice-level unique key in **both** billing and settlement/payment records,",
        "   OR a documented, validated unique linkage alternative (not account+bill-date alone).",
        "2. Billing date, payment due date, invoice amount and cancellation/credit status.",
        "3. Verified `bill_settled_dtm` with exact meaning; **or** dated, invoice-linked payment",
        "   transactions with amount and posting/effective timestamp.",
        "4. Source snapshot or transaction history so features and labels are reconstructed",
        "   **as they were known at month-end T**, not using later information.",
        "5. Stable masked identifiers and the masking / join contract; confirm whether",
        "   masking is consistent *within* years and across sources. Do not assume cross-year joins.",
        "6. SLT sign-off on outcome cutoff, partially paid bills, grace period, reporting lag,",
        "   and whether suspension is a separate target (dates/reasons are still pending).",
        "7. At least one fully observed historical period and independent time-based testing.",
        "", "## Why Step 37 is insufficient on its own",
        "- Step 37 examined score/rank sensitivity to draft weights on synthetic prototype scores.",
        "- Stability of ranking is not evidence that the weights predict future non-payment.",
        "- Do not replace the current 25/20/15/25/10/5 prototype weights on this audit alone.",
        "", "## Next action",
    ]
    if state["timestamp_and_invoice_id_in_same_file"]:
        lines += [
            "A candidate file includes a date and invoice key. Ask the data owner to confirm",
            "field meanings and linkage, then validate a small SLT-approved extract locally.",
        ]
    elif state["timestamp_candidates_found_in"]:
        lines += [
            "A possible settlement/payment-date field was found, but a shared unique invoice",
            "key is not yet evidenced in that file. Request the documented invoice linkage.",
        ]
    else:
        lines += [
            "No confirmed settlement/payment-event date was found in the inspected files.",
            "Request an approved settlement/payment transaction table or its **schema** first.",
        ]
    lines += [
        "No usage files or 2025 billing files are required to complete this discovery step.",
        "", "## Privacy and production boundary",
        "This is private SLT project material: never upload raw billing/payment data,",
        "identifiers, or private audit outputs to public GitHub. Keep the Flask score marked",
        "**PROVISIONAL — NOT STATISTICALLY VALIDATED** pending real label validation.",
    ]
    if errors:
        lines += ["", "## Files with issues"] + [f"- {m}" for m in errors]
    return "\n".join(lines) + "\n"


def request_text(state: dict) -> str:
    return ("Hi Madam,\n\n"
            "We have completed the historical billing feature analysis and the prototype "
            "creditworthiness weight-sensitivity check. To statistically validate the "
            "weights against real one-month-ahead non-payment outcomes, could you please "
            "confirm where `bill_settled_dtm` is stored and share its data dictionary/schema "
            "(a small approved extract is enough initially)?\n\n"
            "We also need to know the unique invoice identifier used to link settlement "
            "or payment records to our DIM_BILL files, whether the masked IDs match across "
            "sources, and whether payment/outstanding amounts represent a particular "
            "month-end snapshot or can change later.\n\n"
            "Our proposed target is: at month-end, predict whether the account will have "
            "a bill due next month that remains unpaid by that next month-end. Please "
            "confirm the grace period and how partially paid bills should be classified.\n\n"
            "We will keep the current scoring weights provisional until this is "
            "verified. Thank you.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extra-file", type=Path, action="append", default=[],
                        help="Inspect one additional approved CSV or Parquet schema (repeatable)")
    parser.add_argument("--scan-dir", type=Path,
                        help="Inspect CSV/Parquet schemas in one extra approved folder")
    args = parser.parse_args()
    print("\nSLT STEP 38 — PAYMENT OUTCOME SOURCE DISCOVERY")
    print("Mode: metadata / headers ONLY. No identifiers or customer records displayed.")
    print("No new scores, labels, models, or weight approvals will be generated.\n")
    paths, issues = collect_files(args)
    inspected: list[dict] = []
    for p in paths:
        try:
            record = one_file(p)
            inspected.append(record)
            print(f"{p.name}: {record['column_count']} columns; "
                  f"settlement-date candidate: {'YES' if record['settlement_date_fields'] else 'NO'}; "
                  f"dated-payment candidate: {'YES' if record['payment_event_date_fields'] else 'NO'}; "
                  f"invoice ID candidate: {'YES' if record['invoice_key_fields'] else 'NO'}")
        except Exception as exc:
            issues.append(f"{p.name}: {type(exc).__name__}: {exc}")
            print(f"Could not inspect {p.name}: {type(exc).__name__}: {exc}")
    if not inspected:
        print("No schemas could be inspected. Check data/historical and pyarrow installation.")
    result = source_state(inspected)
    result.update({
        "step": 38,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "inspected_files": len(inspected),
        "file_errors_or_warnings": issues,
        "all_data_read": "metadata_or_header_only",
        "outcome_label_status": "HOLD - awaiting dated, joinable, point-in-time evidence",
        "no_new_scores_or_labels_created": True,
    })
    PRIVATE.mkdir(parents=True, exist_ok=True)
    csv_path = PRIVATE / "step38_field_inventory.csv"
    json_path = PRIVATE / "step38_outcome_source_readiness.json"
    md_path = PRIVATE / "step38_label_data_contract.md"
    request_path = PRIVATE / "step38_data_request_to_madam.txt"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(inspected)
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    md_path.write_text(make_contract(inspected, result, issues, result["generated_utc"]),
                       encoding="utf-8")
    request_path.write_text(request_text(result), encoding="utf-8")
    for file in (csv_path, md_path, json_path, request_path):
        print("Saved:", file.relative_to(ROOT))
    print("\nDECISION: HOLD on statistical validation of scoring weights until labels are verified.")
    return 0 if inspected else 1


if __name__ == "__main__":
    raise SystemExit(main())
