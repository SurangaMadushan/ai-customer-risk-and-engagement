"""SLT Step 26: summarize historical-data/model readiness using aggregate-only Step 23/25 reports.

This script reads existing aggregate JSON reports; it NEVER reads raw Parquet,
prints customer IDs, changes the Flask website, creates scores, or trains a model.

Run from the SLT_Payment_Project root:
    python 26_assess_scoring_readiness.py

Outputs (keep local/private):
    outputs/private_audit/step26_model_readiness_review.json
    outputs/private_audit/step26_model_readiness_review.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Required report not found: {path}. Run Step 23 and Step 25 full validation first.")
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Unexpected report structure: {path.name}")
    return value


def percent(numerator: int, denominator: int) -> float | None:
    return round(100.0 * numerator / denominator, 2) if denominator else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only, aggregate SLT billing model-readiness review")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "outputs" / "private_audit",
                        help="Directory containing Step 23 and Step 25 aggregate reports")
    args = parser.parse_args()
    report_dir = args.report_dir.resolve()

    print("\nSLT STEP 26 — SCORING AND PREDICTION DATA READINESS")
    print("Reads only existing aggregate reports; no customer identifiers are printed.")
    try:
        validation = read_json(report_dir / "step23_billing_validation.json")
        account_report = read_json(report_dir / "step25_billing_feature_report_full.json")
        if validation.get("mode") != "full":
            raise ValueError("Step 23 report is not full validation. Run: python 23_validate_historical_billing.py")
        if account_report.get("mode") != "all_rows":
            raise ValueError("Step 25 full report is missing full-data mode. Run: python 25_customer_billing_features.py")
        if validation.get("errors"):
            raise ValueError("Step 23 reported errors. Review and resolve them before proceeding.")

        year_rows = sorted(account_report.get("yearly", []), key=lambda r: int(r["year"]))
        if not year_rows:
            raise ValueError("No yearly account aggregates in Step 25 report")
        years = [int(r["year"]) for r in year_rows]
        rows = []
        for r in year_rows:
            unique = int(r["distinct_masked_accounts"])
            any_positive = int(r["accounts_with_any_positive_outstanding_row"])
            if unique <= 0 or not (0 <= any_positive <= unique):
                raise ValueError(f"Invalid account totals for {r['year']}")
            rows.append({
                "year": int(r["year"]),
                "unique_masked_accounts": unique,
                "accounts_with_any_positive_outstanding_row": any_positive,
                "percent_with_any_positive_outstanding_row": percent(any_positive, unique),
                "billing_records_with_account_id": int(r["billing_records_with_account_id"]),
            })

        overlaps = []
        for item in account_report.get("cross_year_id_overlap", []):
            overlaps.append({
                "from_year": int(item["from_year"]),
                "to_year": int(item["to_year"]),
                "matching_masked_ids": int(item["matching_masked_account_ids"]),
                "percent_of_earlier_accounts": item.get("percent_of_earlier_accounts"),
                "gap_in_calendar_years": int(item.get("gap_in_calendar_years", 0)),
            })

        unusual_due = sum(int(item.get("due_more_than_365_days_after_bill", 0))
                          for item in validation.get("files", []))
        missing_years = sorted(set(int(x) for x in account_report.get("missing_years", [])) |
                               set(int(x) for x in validation.get("missing_years", [])))
        all_records = sum(int(r["billing_records_with_account_id"]) for r in rows)
        declared = int(validation.get("total_processed_rows", 0))
        if all_records != declared:
            print(f"WARNING: Step 25 usable billing rows ({all_records:,}) differ from Step 23 processed rows ({declared:,}).")

        comparison = None
        prev = next((r for r in rows if r["year"] == 2024), None)
        current = next((r for r in rows if r["year"] == 2026), None)
        if prev and current:
            comparison = {
                "percent_2024": prev["percent_with_any_positive_outstanding_row"],
                "percent_2026": current["percent_with_any_positive_outstanding_row"],
                "difference_percentage_points": round(current["percent_with_any_positive_outstanding_row"] -
                                                       prev["percent_with_any_positive_outstanding_row"], 2),
            }

        blockers = [
            "Confirm whether masked account_no is stable and authorized for cross-year linkage.",
            "Obtain the missing 2025 billing file or document the gap and its effect on time-based validation.",
            "Confirm whether outstanding_debt_amt is an invoice balance or repeated account-level snapshot and explain the 2026 increase.",
            "Confirm the accounting meaning and period of payments_amt; there is no individual payment transaction date.",
            "Obtain dated payment transactions and, for suspension prediction, suspension dates and reasons.",
            "Agree with SLT on the prediction outcome, horizon (e.g. 30/60/90 days), and business-use policy.",
            "Review unusual payment due dates more than 365 days after bill dates before deriving labels.",
        ]
        payload = {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "source_reports": ["step23_billing_validation.json", "step25_billing_feature_report_full.json"],
            "years": years,
            "source_billing_rows_processed": declared,
            "account_year_statistics": rows,
            "cross_year_masked_id_overlap": overlaps,
            "missing_years": missing_years,
            "due_dates_more_than_365_days_later": unusual_due,
            "positive_outstanding_2024_vs_2026": comparison,
            "readiness": "HOLD — billing exploration is possible; final risk score and future nonpayment model are not validated",
            "blockers_to_resolve": blockers,
            "interpretation": [
                "'Any positive outstanding row' is NOT a count of defaulters or a future nonpayment label.",
                "One masked account can appear in multiple billing rows and multiple years.",
                "A high masked-ID match rate does not prove stable anonymization across years.",
                "The 2026 file is partial-year. The 2025 year is missing.",
                "Do not derive model weights or train predictions from the current summaries alone.",
                "No private customer identifiers are exported by this script.",
            ],
        }

        report_dir.mkdir(parents=True, exist_ok=True)
        json_path = report_dir / "step26_model_readiness_review.json"
        md_path = report_dir / "step26_model_readiness_review.md"
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        lines = [
            "# SLT Customer Risk Intelligence — Step 26 Readiness Review", "",
            "**Status: HOLD for validated scoring / predictive deployment.** Billing analysis can proceed.", "",
            f"Billing rows validated: **{declared:,}**. Years: **{', '.join(map(str, years))}**.", "",
            "## Account-year observations", "",
            "| Year | Unique masked accounts | Accounts with any positive outstanding row | Share |",
            "|---|---:|---:|---:|",
        ]
        for r in rows:
            lines.append(f"| {r['year']} | {r['unique_masked_accounts']:,} | "
                         f"{r['accounts_with_any_positive_outstanding_row']:,} | "
                         f"{r['percent_with_any_positive_outstanding_row']:.2f}% |")
        lines += [
            "", "These are descriptive account-level statistics, **not non-payment/default rates**.",
            "", "## Masked ID overlap", "",
            "| From | To | Matching masked IDs | % of earlier year's accounts |",
            "|---|---|---:|---:|",
        ]
        for item in overlaps:
            share = item["percent_of_earlier_accounts"]
            lines.append(f"| {item['from_year']} | {item['to_year']} | "
                         f"{item['matching_masked_ids']:,} | "
                         f"{share if share is not None else 'n/a'}% |")
        lines += ["", "**Warning:** Cross-year matches must be confirmed with the SLT data owner; "
                  "2024→2026 skips a year.", ""]
        if comparison:
            lines.append(f"2024 vs 2026: {comparison['percent_2024']:.2f}% vs "
                         f"{comparison['percent_2026']:.2f}% of accounts have at least one "
                         "positive outstanding record. **Investigate definitions and coverage; "
                         "do not interpret as a default-rate increase.**")
            lines.append("")
        lines += [f"Unusual due-date records (>365 days after bill date): **{unusual_due:,}**.", "",
                  f"Missing year(s): **{', '.join(map(str, missing_years)) if missing_years else 'none'}**.", "",
                  "## Questions / data required before validated scoring", ""]
        lines += [f"{i}. {q}" for i, q in enumerate(blockers, 1)]
        lines += ["", "## Next recommended technical work", "",
                  "Continue read-only billing diagnostics. Once the payment field meanings and "
                  "dated payment outcomes are available, build account-month features using only "
                  "information known at each prediction date, then train and evaluate a "
                  "time-split, calibrated baseline (e.g. logistic regression). Compare other "
                  "models, document metrics and only then approve a creditworthiness score.", "",
                  "**Privacy:** Keep source data, reports, and DuckDB files in approved local "
                  "storage and outside public GitHub.", ""]
        md_path.write_text("\n".join(lines), encoding="utf-8")

        print("\nYear | Unique accounts | Any positive outstanding | Share")
        for r in rows:
            print(f"{r['year']} | {r['unique_masked_accounts']:,} | "
                  f"{r['accounts_with_any_positive_outstanding_row']:,} | "
                  f"{r['percent_with_any_positive_outstanding_row']:.2f}%")
        print("\nCross-year masked account matches:")
        for item in overlaps:
            print(f" {item['from_year']} -> {item['to_year']}: {item['matching_masked_ids']:,} "
                  f"({item['percent_of_earlier_accounts']}% of earlier year's accounts)")
        print("\nMissing year(s):", missing_years or "none")
        print("Unusual due dates >365 days:", f"{unusual_due:,}")
        print("READINESS: HOLD — validated scoring needs confirmed data meanings and outcomes.")
        print("Report saved:", md_path)
        print("JSON saved:", json_path)
        print("RESULT: Aggregate readiness review completed; no risk scores generated.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
