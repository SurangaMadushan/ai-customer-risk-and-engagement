"""Validate the local SLT Flask website against the approved 200-account dummy workbook.

Run from the project root with its virtual environment:
    python 16_validate_flask_website.py

Read-only checks: does not alter customer scores, recommendations, or source data.
Flask's test client tests pages in memory; the live website need not be stopped.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
WORKBOOK = ROOT / "data" / "SLT_Payment_Behaviour_Dummy_Data.xlsx"
SCORES = ROOT / "outputs" / "customer_creditworthiness.csv"
RECOMMENDATIONS = ROOT / "outputs" / "collection_recommendations.csv"
APP_PATH = ROOT / "web_app" / "app.py"
REPORT_PATH = ROOT / "outputs" / "website_validation_report.txt"

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))


def normalize_id(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def safe_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def main() -> int:
    print("SLT WEBSITE VALIDATION — DUMMY DATA ONLY")
    print("=" * 56)

    missing = [str(p.relative_to(ROOT)) for p in (WORKBOOK, SCORES, RECOMMENDATIONS, APP_PATH) if not p.is_file()]
    if missing:
        check("Required files present", False, ", ".join(missing))
        return finish()
    check("Required files present", True)

    try:
        scores = pd.read_csv(SCORES, dtype={"account_no": "string", "customer_id": "string"})
        recs = pd.read_csv(RECOMMENDATIONS, dtype={"account_no": "string", "customer_id": "string"})
        master = pd.read_excel(WORKBOOK, sheet_name="Customer_Master", engine="openpyxl")
        summary = pd.read_excel(WORKBOOK, sheet_name="Behaviour_Summary", engine="openpyxl")
        billing = pd.read_excel(WORKBOOK, sheet_name="Billing", engine="openpyxl")
        payments = pd.read_excel(WORKBOOK, sheet_name="Payments", engine="openpyxl")
    except Exception as exc:
        check("Load dummy workbook and output CSV files", False, repr(exc))
        return finish()
    check("Load dummy workbook and output CSV files", True)

    for df in (scores, recs, master, summary, billing, payments):
        df["account_no"] = normalize_id(df["account_no"])
    for df in (scores, recs, master, summary):
        df["customer_id"] = normalize_id(df["customer_id"])

    check("One unique account per output", len(scores) == len(recs) == len(master) == len(summary)
          and all(not df["account_no"].isna().any() and df["account_no"].is_unique
                  for df in (scores, recs, master, summary)),
          f"scores={len(scores)} recommendations={len(recs)} master={len(master)} summary={len(summary)}")

    def id_pairs(df: pd.DataFrame) -> set[tuple[str, str]]:
        return set(zip(df["account_no"].tolist(), df["customer_id"].tolist()))

    check("Customer IDs match across source and output files",
          id_pairs(scores) == id_pairs(recs) == id_pairs(master) == id_pairs(summary))

    check("Billing and payments reference known accounts",
          billing["account_no"].isin(master["account_no"]).all()
          and payments["account_no"].isin(master["account_no"]).all(),
          f"billing_rows={len(billing)} payment_rows={len(payments)}")

    score_numeric = safe_number(scores["payment_reliability_score"])
    check("Payment-reliability scores are numeric and within 0–100",
          score_numeric.notna().all() and score_numeric.between(0, 100).all())

    point_cols = [
        "points_settlement", "points_consistency", "points_delay",
        "points_current_arrears", "points_balance_exposure", "points_collection_history",
    ]
    if all(c in scores.columns for c in point_cols):
        summed = scores[point_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1)
        check("Score agrees with its six components (rounding tolerance 0.11)",
              (summed - score_numeric).abs().le(0.11).all())
    else:
        check("Creditworthiness score components available", False,
              "Missing: " + ", ".join(c for c in point_cols if c not in scores.columns))

    allowed = {"No follow-up", "Normal follow-up", "Priority follow-up"}
    check("Recommendations use the three allowed actions",
          recs["recommended_action"].notna().all()
          and set(recs["recommended_action"].unique()) <= allowed,
          str(recs["recommended_action"].value_counts().to_dict()))
    check("Every recommendation has a written reason",
          recs["recommendation_reason"].astype("string").str.strip().fillna("").ne("").all())

    outstanding = safe_number(scores["current_outstanding_LKR"])
    overdue_days = safe_number(scores["current_overdue_days"])
    unpaid = safe_number(scores["unpaid_past_due_bills"])
    is_overdue = unpaid.gt(0) & overdue_days.gt(0) & outstanding.gt(0)
    summary_overdue = (
        safe_number(summary["unpaid_past_due_bills"]).gt(0)
        & safe_number(summary["current_overdue_days"]).gt(0)
        & safe_number(summary["current_outstanding_LKR"]).gt(0)
    )
    expected = set(summary.loc[summary_overdue, "account_no"])
    actual = set(scores.loc[is_overdue, "account_no"])
    check("Overdue accounts match original dummy workbook", actual == expected,
          f"{len(actual)} identified; {len(expected)} in original workbook")

    source_sum = float(safe_number(summary.loc[summary_overdue, "current_outstanding_LKR"]).sum())
    derived_sum = float(outstanding.loc[is_overdue].sum())
    check("Overdue outstanding matches workbook to 1 cent",
          math.isclose(source_sum, derived_sum, abs_tol=0.01),
          f"LKR {derived_sum:,.2f}")

    merged = scores[["account_no"]].copy()
    merged["is_overdue"] = is_overdue.values
    merged = merged.merge(recs[["account_no", "recommended_action"]], on="account_no", validate="one_to_one")
    bad_followup = merged.loc[merged["is_overdue"] & merged["recommended_action"].eq("No follow-up")]
    check("No overdue account marked No follow-up", bad_followup.empty,
          f"violations={len(bad_followup)}")

    try:
        # Load the existing app without starting a separate server.
        spec = importlib.util.spec_from_file_location("slt_web_validation", APP_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load web_app/app.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        client = mod.app.test_client()
        routes = {
            "/": "Home page",
            "/customers": "Customer directory",
            "/customers?q=C10274": "Customer ID search",
            "/customers?q=401934": "Account number search",
            "/customers/401934": "Customer profile",
            "/creditworthiness": "Creditworthiness page",
            "/collections": "Collection page",
            "/collections?action=Priority+follow-up": "Filtered collection page",
        }
        for url, label in routes.items():
            response = client.get(url)
            check(f"Flask: {label}", response.status_code == 200,
                  f"HTTP {response.status_code}")
        response = client.get("/customers/999999999999999999")
        check("Flask: unknown account returns 404", response.status_code == 404,
              f"HTTP {response.status_code}")
        profile_body = client.get("/customers/401934").get_data(as_text=True)
        check("Flask: customer profile displays correct customer", "C10274" in profile_body)
        check("Flask: profile displays recommendation and creditworthiness",
              "Creditworthiness" in profile_body and "Collection" in profile_body)
    except ImportError as exc:
        check("Flask available in this virtual environment", False,
              f"{exc} — run python -m pip install -r web_app\\requirements.txt")
    except Exception as exc:
        check("Flask page test completed", False, repr(exc))

    return finish()


def finish() -> int:
    passed = sum(ok for _, ok, _ in checks)
    failed = len(checks) - passed
    result = "PASS" if failed == 0 else "FAIL — review the failed checks"
    report = [
        "SLT Customer Intelligence Website — Local Validation Report",
        "Dummy dataset / development use only",
        "Generated: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        f"Result: {result}",
        f"Checks passed: {passed} / {len(checks)}",
        "",
    ]
    report.extend(
        f"{'PASS' if ok else 'FAIL'}: {name}" + (f" | {detail}" if detail else "")
        for name, ok, detail in checks
    )
    try:
        REPORT_PATH.parent.mkdir(exist_ok=True)
        REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"\nReport saved: {REPORT_PATH}")
    except OSError as exc:
        print(f"\nUnable to write report: {exc}")
    print(f"\nValidation result: {result} ({passed}/{len(checks)} checks passed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
