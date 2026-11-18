#!/usr/bin/env python3
"""
SLT STEP 36 — Preliminary Statistical Diagnostics for Creditworthiness Factors

WHAT THIS DOES:
  * Uses the existing private Step 32 creditworthiness-feature DuckDB.
  * Checks associations (Pearson correlations), missing values and yearly
    patterns among available DESCRIPTIVE billing/payment proxies.
  * Produces aggregate-only CSV, Markdown and JSON evidence reports.

WHAT THIS DOES NOT DO:
  * It does NOT validate the 25/20/15/25/10/5 scoring weights.
  * It does NOT create customers' credit scores or default labels.
  * It does NOT estimate non-payment probabilities.
  * It does NOT assume past-month payment amounts were known at that time.
  * It does NOT infer invoice settlement or payment dates.

Why not? The five inspected source Parquet files do not contain confirmed
invoice-level settlement dates, complete dated payment transactions, or
time-stamped balance snapshots needed for reliable historical outcome labels.

Run from the SLT_Payment_Project root:
    python 36_creditworthiness_statistical_diagnostics.py --sample
    python 36_creditworthiness_statistical_diagnostics.py

Optional: run a larger deterministic sample (e.g. 5%):
    python 36_creditworthiness_statistical_diagnostics.py --sampling-per-thousand 50

Optional: full aggregate scan (slower):
    python 36_creditworthiness_statistical_diagnostics.py --sampling-per-thousand 1000

All reports are PRIVATE. Do not upload outputs/private_audit to public GitHub.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "outputs" / "private_audit"
YEARS = (2021, 2022, 2023, 2024, 2026)

# Conceptual descriptors only: these are proxies, NOT verified financial
# positions or point-in-time features, nor the six approved scoring factors.
FEATURES = [
    ("payment_month_share", "Prior 3-month positive recorded-payment frequency"),
    ("debt_month_share", "Prior 3-month positive-debt indicator frequency"),
    ("recorded_payment_signed_log", "Signed log of prior 3-month recorded payment sum"),
    ("billed_amount_signed_log", "Signed log of prior 3-month billed row amount sum"),
    ("max_debt_row_signed_log", "Signed log of maximum prior 3-month debt row"),
]
FEATURE_NAMES = [f[0] for f in FEATURES]

# These weights come from the Flask RULE-BASED PROTOTYPE, not from an
# empirically validated statistical model.
FACTORS = [
    ("Settled bill history", 25, "NOT MEASURABLE", "Confirmed invoice settlement status / dates"),
    ("Payment consistency", 20, "PARTIAL PROXY", "Dated invoice-linked settlements to verify on-time consistency"),
    ("Average payment delay", 15, "NOT MEASURABLE", "bill_settled_dtm or dated invoice-linked payments"),
    ("Current arrears", 25, "PARTIAL PROXY", "Point-in-time overdue balance and invoice eligibility"),
    ("Outstanding exposure", 10, "PARTIAL PROXY", "Verified account-level balance definition and snapshot timing"),
    ("Collection history", 5, "NOT MEASURABLE", "Dated collection and suspension records"),
]
REQUIRED = {
    "masked_account_id",
    "feature_month",
    "history_3m_observed_billing_months",
    "history_3m_positive_net_payment_months",
    "history_3m_debt_indicator_month_share",
    "history_3m_recorded_payment_row_sum",
    "history_3m_billed_row_sum",
    "history_3m_max_outstanding_row_UNVERIFIED",
}

CORRELATION_COLS = [
    "year", "proxy_feature_a", "proxy_feature_b", "sampled_rows",
    "paired_non_null", "pearson_r", "abs_pearson_r", "interpretation",
]
PROFILE_COLS = [
    "year", "proxy_feature", "description", "sampled_rows",
    "non_null", "mean", "stddev", "min", "max",
]
STATUS_COLS = [
    "creditworthiness_factor", "current_prototype_points",
    "available_evidence", "missing_data", "predictive_weight_validated",
]


def save_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def num(v: object, precision: int = 5):
    if v is None:
        return None
    try:
        f = float(v)
        return round(f, precision) if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def signed_log(col: str) -> str:
    # Handles negative adjustments without treating them as invalid.
    return (
        f"CASE WHEN TRY_CAST({col} AS DOUBLE) IS NULL THEN NULL "
        f"WHEN TRY_CAST({col} AS DOUBLE) < 0 "
        f"THEN -LN(1 + ABS(TRY_CAST({col} AS DOUBLE))) "
        f"ELSE LN(1 + TRY_CAST({col} AS DOUBLE)) END"
    )


def build_query(year: int, per_thousand: int, min_history: int) -> str:
    table = f"credit_features_{year}"
    sampling = (
        "" if per_thousand == 1000 else
        f"AND (hash(CAST(masked_account_id AS VARCHAR) || ':' || "
        f"CAST(feature_month AS VARCHAR)) % 1000) < {per_thousand}"
    )
    # For the full run, use only complete three-month history (3).
    # The 20k-row demo sample is sparse, so use >=1 to smoke-test queries.
    cols = [
        "COUNT(*)::BIGINT AS sampled_rows",
    ]
    for i, name in enumerate(FEATURE_NAMES):
        cols += [
            f"COUNT({name})::BIGINT AS n{i}",
            f"AVG({name}) AS mean{i}",
            f"STDDEV_SAMP({name}) AS sd{i}",
            f"MIN({name}) AS min{i}",
            f"MAX({name}) AS max{i}",
        ]
    for i in range(len(FEATURE_NAMES)):
        for j in range(i + 1, len(FEATURE_NAMES)):
            a = FEATURE_NAMES[i]
            b = FEATURE_NAMES[j]
            cols += [
                f"COUNT(*) FILTER (WHERE {a} IS NOT NULL AND "
                f"{b} IS NOT NULL)::BIGINT AS pair_n{i}_{j}",
                f"CORR({a}, {b}) AS corr{i}_{j}",
            ]
    return f"""
    WITH selected AS (
        SELECT
            TRY_CAST(history_3m_positive_net_payment_months AS DOUBLE) /
                NULLIF(TRY_CAST(history_3m_observed_billing_months AS DOUBLE), 0)
                    AS payment_month_share,
            TRY_CAST(history_3m_debt_indicator_month_share AS DOUBLE)
                    AS debt_month_share,
            {signed_log("history_3m_recorded_payment_row_sum")}
                    AS recorded_payment_signed_log,
            {signed_log("history_3m_billed_row_sum")}
                    AS billed_amount_signed_log,
            {signed_log("history_3m_max_outstanding_row_UNVERIFIED")}
                    AS max_debt_row_signed_log
        FROM {table}
        WHERE history_3m_observed_billing_months >= {min_history}
        {sampling}
    )
    SELECT {", ".join(cols)}
    FROM selected
    """


def interpretation(r: float | None) -> str:
    if r is None:
        return "Not estimable (insufficient variation or paired observations)"
    if abs(r) >= 0.80:
        return "Strong correlation; investigate possible redundancy"
    if abs(r) >= 0.60:
        return "Moderate-to-strong correlation; check overlap"
    if abs(r) >= 0.30:
        return "Some linear association; not predictive evidence"
    return "Weak linear association; not proof of no relationship"


def read_report_status(mode: str) -> dict:
    path = PRIVATE / f"step34_target_readiness_{mode}.json"
    if not path.is_file():
        return {"target_readiness_report": "not found"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        return {"target_readiness_report": "found", "report_type": type(doc).__name__}
    except (OSError, ValueError):
        return {"target_readiness_report": "unreadable"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true", help="Use Step 32's sample database")
    parser.add_argument("--sampling-per-thousand", type=int, default=None,
                        help="Deterministic sample rate: 1 to 1000 (1000 = all eligible rows)")
    parser.add_argument("--memory-limit", default="1GB",
                        help="DuckDB memory limit (e.g. 1GB, 2GB, 2048MB)")
    args = parser.parse_args()
    if args.sampling_per_thousand is not None and not 1 <= args.sampling_per_thousand <= 1000:
        parser.error("--sampling-per-thousand must be between 1 and 1000")
    if not re.fullmatch(r"\d+(?:GB|MB)", args.memory_limit.upper()):
        parser.error("--memory-limit must look like 1GB or 2048MB")

    try:
        import duckdb
    except ImportError:
        print("ERROR: DuckDB is not installed. Run: python -m pip install duckdb")
        return 1

    mode = "sample" if args.sample else "full"
    rate = args.sampling_per_thousand or (1000 if args.sample else 10)
    min_history = 1 if args.sample else 3
    db = PRIVATE / f"step32_private_creditworthiness_features_{mode}.duckdb"
    if not db.is_file():
        print(f"ERROR: Step 32 database not found:\n  {db}")
        print("Run Step 32 in the same mode first.")
        return 1
    PRIVATE.mkdir(parents=True, exist_ok=True)

    print("\nSLT STEP 36 — CREDITWORTHINESS STATISTICAL DIAGNOSTICS")
    print(f"Mode: {mode.upper()} | 5 years | Sample: {rate / 10:.1f}% of eligible records")
    print(f"History requirement: >= {min_history} preceding billing month(s)")
    print("No customer IDs, risk scores, model targets or predictions will be exported.")
    print("WARNING: Correlations CANNOT establish appropriate creditworthiness weights.")

    profiles: list[dict] = []
    correlations: list[dict] = []
    yearly: list[dict] = []
    con = None
    try:
        con = duckdb.connect(str(db), read_only=True)
        con.execute(f"SET memory_limit = '{args.memory_limit.upper()}'")
        present_tables = {
            row[0] for row in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }

        for year in YEARS:
            table = f"credit_features_{year}"
            if table not in present_tables:
                raise RuntimeError(f"Missing table {table}. Re-run Step 32 ({mode}).")
            cols = {r[1] for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()}
            missing = REQUIRED - cols
            if missing:
                raise RuntimeError(
                    f"{table} is missing expected features: {', '.join(sorted(missing))}"
                )

            result = con.execute(build_query(year, rate, min_history))
            colnames = [item[0] for item in result.description]
            row = dict(zip(colnames, result.fetchone()))
            n = int(row["sampled_rows"] or 0)
            print(f"  {year}: {n:,} sampled account-month feature rows")
            yearly.append({"year": year, "sampled_rows": n})

            for i, (name, description) in enumerate(FEATURES):
                profiles.append({
                    "year": year, "proxy_feature": name,
                    "description": description,
                    "sampled_rows": n,
                    "non_null": int(row[f"n{i}"] or 0),
                    "mean": num(row[f"mean{i}"]),
                    "stddev": num(row[f"sd{i}"]),
                    "min": num(row[f"min{i}"]),
                    "max": num(row[f"max{i}"]),
                })
            for i in range(len(FEATURES)):
                for j in range(i + 1, len(FEATURES)):
                    v = num(row[f"corr{i}_{j}"])
                    correlations.append({
                        "year": year,
                        "proxy_feature_a": FEATURE_NAMES[i],
                        "proxy_feature_b": FEATURE_NAMES[j],
                        "sampled_rows": n,
                        "paired_non_null": int(row[f"pair_n{i}_{j}"] or 0),
                        "pearson_r": v,
                        "abs_pearson_r": num(abs(v)) if v is not None else None,
                        "interpretation": interpretation(v),
                    })

    except Exception as exc:
        print(f"\nERROR: Statistical diagnostic query could not complete: {exc}")
        print("Source files were not changed. If this is a memory error, try --memory-limit 2GB.")
        return 1
    finally:
        if con is not None:
            con.close()

    weight_rows = [
        {
            "creditworthiness_factor": factor,
            "current_prototype_points": points,
            "available_evidence": availability,
            "missing_data": missing,
            "predictive_weight_validated": "NO",
        }
        for factor, points, availability, missing in FACTORS
    ]

    prefix = PRIVATE / f"step36"
    p_profile = Path(f"{prefix}_feature_profiles_{mode}.csv")
    p_corr = Path(f"{prefix}_proxy_correlations_{mode}.csv")
    p_weight = Path(f"{prefix}_weight_validation_status_{mode}.csv")
    p_md = Path(f"{prefix}_statistical_diagnostics_{mode}.md")
    p_json = Path(f"{prefix}_statistical_diagnostics_{mode}.json")

    save_csv(p_profile, PROFILE_COLS, profiles)
    save_csv(p_corr, CORRELATION_COLS, correlations)
    save_csv(p_weight, STATUS_COLS, weight_rows)

    strong_pairs = [
        r for r in correlations
        if r["abs_pearson_r"] is not None and r["abs_pearson_r"] >= 0.80
    ]
    insufficient_years = [r["year"] for r in yearly if r["sampled_rows"] < 30]
    output = {
        "step": 36,
        "mode": mode,
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "sampling_per_thousand": rate,
        "minimum_prior_observed_billing_months": min_history,
        "sampled_rows_per_year": yearly,
        "strong_proxy_correlations_count": len(strong_pairs),
        "years_with_fewer_than_30_sampled_eligible_rows": insufficient_years,
        "provisional_weight_total_points": sum(x[1] for x in FACTORS),
        "weights_statistically_validated": False,
        "verified_future_nonpayment_labels": False,
        "verified_point_in_time_payment_records": False,
        "can_train_validated_predictive_creditworthiness_model": False,
        "step34_status": read_report_status(mode),
    }
    p_json.write_text(json.dumps(output, indent=2), encoding="utf-8")

    md = [
        "# SLT Step 36 — Preliminary Statistical Diagnostics",
        "",
        f"**Mode:** {mode.upper()}  ",
        f"**Deterministic sample rate:** {rate / 10:.1f}% of eligible rows  ",
        f"**Minimum previous billing months observed:** {min_history}  ",
        "",
        "## What the analysis actually establishes",
        "",
        "- Descriptive distributions and pairwise Pearson correlations among five available billing/payment proxies.",
        "- Whether selected proxies may carry overlapping information and need further examination.",
        "- Which of the six existing scoring factors are partially measurable versus unavailable.",
        "",
        "**It does NOT establish that the 25/20/15/25/10/5 scoring weights are statistically justified.**",
        "Correlations between proxies are not correlations with future non-payment.",
        "",
        "## Sampled feature observations",
        "",
        "| Year | Sampled account-month rows |",
        "|---|---:|",
    ]
    for r in yearly:
        md.append(f"| {r['year']} | {r['sampled_rows']:,} |")
    md += ["", "## Potentially overlapping proxy pairs (absolute Pearson r >= 0.80)", ""]
    if strong_pairs:
        md += [
            "| Year | Proxy A | Proxy B | r |",
            "|---|---|---|---:|",
        ]
        for r in strong_pairs:
            md.append(
                f"| {r['year']} | {r['proxy_feature_a']} | "
                f"{r['proxy_feature_b']} | {r['pearson_r']} |"
            )
    else:
        md.append("No pairs crossed this heuristic threshold, or available observations were insufficient.")
    md += [
        "",
        "## Six-factor weight validation status",
        "",
        "| Factor | Prototype points | Evidence | Statistically validated? |",
        "|---|---:|---|---|",
    ]
    for r in weight_rows:
        md.append(
            f"| {r['creditworthiness_factor']} | "
            f"{r['current_prototype_points']} | {r['available_evidence']} | NO |"
        )
    md += [
        "",
        "## Why true weight validation remains pending",
        "",
        "1. The current source lacks a confirmed `bill_settled_dtm` or invoice-linked dated payment table.",
        "2. We need a business-approved **one-month-ahead non-payment target** and outcome observation cutoff.",
        "3. Payment and debt values attached to earlier billing months may have been updated later; availability-as-of must be verified.",
        "4. Without valid dated outcome labels, no statistical association with future non-payment can be estimated, and no predictive weights can be learned or evaluated.",
        "5. Cross-year ID matching is avoided because consistency of masking has not been formally established; 2025 is intentionally not required.",
        "",
        "## Once those records are available",
        "",
        "- Construct point-in-time customer histories and exact one-month-ahead labels without future-data leakage.",
        "- Compare an interpretable logistic-regression model with a suitable tree model on chronological train/validation/test splits.",
        "- Report precision, recall, average precision, calibration and uncertainty.",
        "- Examine factor contributions with interpretable model coefficients and held-out feature importance.",
        "- Obtain SLT sign-off before using any scoring thresholds or predictions for customer actions.",
        "",
        "## Interpretation cautions",
        "",
        "- This is a deterministic sample of account-months, not a random customer-level or independent sample.",
        "- Several observations can belong to the same account; no p-values or independence claims are made.",
        "- Signed logarithms are used for financial amounts to reduce outlier dominance; they are research transformations, not validated financial aggregates.",
        "- 2026 is a partial year, so cross-year differences may reflect coverage or source changes.",
        "- Similar proxies can be redundant; lack of linear correlation does not imply irrelevance.",
        "- All outputs are aggregate-only and should remain under `outputs/private_audit`.",
        "",
    ]
    if insufficient_years:
        md += [
            f"**Insufficient sample size warning:** years {', '.join(map(str, insufficient_years))} "
            "have fewer than 30 eligible sampled rows. Treat those correlations as inconclusive.",
            "",
        ]
    p_md.write_text("\n".join(md), encoding="utf-8")

    print("\nReports saved:")
    for path in (p_profile, p_corr, p_weight, p_md, p_json):
        print(" ", path.relative_to(ROOT))
    print("\nRESULT: Statistical proxy diagnostics completed.")
    print("STATUS: CREDITWORTHINESS WEIGHTS REMAIN PROVISIONAL / NOT VALIDATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
