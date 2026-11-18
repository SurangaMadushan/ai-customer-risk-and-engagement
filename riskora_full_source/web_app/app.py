"""SLT Customer Payment Intelligence — local Flask website prototype.

Run from SLT_Payment_Project root:
    python web_app/app.py

Reads approved synthetic workbook + Step 14/15 CSV outputs. No customer
updates, no automated collection actions, no credentials, no remote access.
DO NOT expose this prototype on a public network or connect sensitive data.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import math

import pandas as pd
from flask import Flask, abort, render_template, request

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"
WORKBOOK = DATA_DIR / "SLT_Payment_Behaviour_Dummy_Data.xlsx"
SCORES = OUTPUT_DIR / "customer_creditworthiness.csv"
RECOMMENDATIONS = OUTPUT_DIR / "collection_recommendations.csv"

import secrets as _consent_demo_secrets
import importlib.util as _consent_demo_importlib
_consent_path = Path(__file__).resolve().parent / 'consent_demo.py'
_consent_spec = _consent_demo_importlib.spec_from_file_location('slt_consent_demo', _consent_path)
_consent_module = _consent_demo_importlib.module_from_spec(_consent_spec)
_consent_spec.loader.exec_module(_consent_module)
consent_bp = _consent_module.bp

app = Flask(__name__)
app.secret_key = _consent_demo_secrets.token_hex(32)  # LOCAL DEMO ONLY
app.register_blueprint(consent_bp)
# >>> STEP 19 CONSENT ACCESS >>>
from consent_access import bp as _step19_access_bp
app.extensions['slt_step19_data_context'] = lambda: context()
app.register_blueprint(_step19_access_bp)
# <<< STEP 19 CONSENT ACCESS <<<
app.config.update(
    TEMPLATES_AUTO_RELOAD=True,
    MAX_CONTENT_LENGTH=16 * 1024,
)


def account_id(values: pd.Series) -> pd.Series:
    return values.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def money(value):
    try:
        v = float(value)
        if not math.isfinite(v):
            return "—"
        return f"LKR {v:,.2f}"
    except (TypeError, ValueError):
        return "—"


def nformat(value):
    try:
        v = float(value)
        return f"{v:,.0f}" if math.isfinite(v) else "—"
    except (TypeError, ValueError):
        return "—"


def fmt_date(value):
    if value is None or pd.isna(value) or str(value).strip() == "":
        return "—"
    dt = pd.to_datetime(value, errors="coerce")
    return dt.strftime("%d %b %Y") if pd.notna(dt) else str(value)


def clean(value):
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return "—"
    if pd.isna(value):
        return "—"
    return value


app.jinja_env.filters["money"] = money
app.jinja_env.filters["number"] = nformat
app.jinja_env.filters["date_short"] = fmt_date
app.jinja_env.filters["clean"] = clean


def required_missing():
    return [str(p.relative_to(ROOT)) for p in (WORKBOOK, SCORES, RECOMMENDATIONS) if not p.is_file()]


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"account_no": "string", "customer_id": "string"})


def ensure_fields(frame: pd.DataFrame, fields: set[str], name: str) -> None:
    missing = fields - set(frame.columns)
    if missing:
        raise ValueError(f"{name} is missing fields: {', '.join(sorted(missing))}")


@lru_cache(maxsize=3)
def load_data(signatures: tuple) -> dict:
    """Cache until inputs change on disk. All datasets are local dummy records."""
    score = read_csv(SCORES)
    rec = read_csv(RECOMMENDATIONS)
    ensure_fields(score, {
        "account_no", "customer_id", "payment_reliability_score", "reliability_band",
        "score_reason", "behaviour_classification", "current_outstanding_LKR",
        "current_overdue_days", "unpaid_past_due_bills",
    }, "customer_creditworthiness.csv")
    ensure_fields(rec, {
        "account_no", "customer_id", "recommended_action", "recommendation_reason",
        "recommendation_rule", "currently_overdue", "requires_human_review",
    }, "collection_recommendations.csv")
    for df, name in ((score, "scores"), (rec, "recommendations")):
        df["account_no"] = account_id(df["account_no"])
        df["customer_id"] = account_id(df["customer_id"])
        if df["account_no"].isna().any() or df["account_no"].duplicated().any():
            raise ValueError(f"Duplicate or missing account IDs in {name}")

    sheets = pd.read_excel(
        WORKBOOK,
        sheet_name=["Customer_Master", "Billing", "Payments", "Collection_History", "Suspension_History"],
        engine="openpyxl",
    )
    for key, table in sheets.items():
        ensure_fields(table, {"account_no"}, f"{key} sheet")
        table["account_no"] = account_id(table["account_no"])
        # Ground-truth/profile labels are for model evaluation, never for this UI.
        sheets[key] = table.loc[:, ~table.columns.str.contains("ground truth|agent_classification", case=False, regex=True)].copy()

    master = sheets["Customer_Master"]
    ensure_fields(master, {"customer_id"}, "Customer_Master sheet")
    if master["account_no"].duplicated().any():
        raise ValueError("Duplicate account IDs in Customer_Master")
    master["customer_id"] = account_id(master["customer_id"])

    mismatch = score[["account_no", "customer_id"]].merge(
        rec[["account_no", "customer_id"]], on="account_no", how="outer", indicator=True,
        suffixes=("_score", "_rec"), validate="one_to_one",
    )
    if (mismatch["_merge"] != "both").any() or not mismatch["customer_id_score"].eq(mismatch["customer_id_rec"]).all():
        raise ValueError("Step 14 and 15 outputs have mismatching account/customer identifiers")

    profiles = score.merge(
        master.drop(columns="customer_id"), on="account_no", how="left", validate="one_to_one",
        suffixes=("", "_master"),
    ).merge(
        rec[["account_no", "recommended_action", "recommendation_reason",
             "recommendation_rule", "currently_overdue", "requires_human_review",
             *(["priority_rank"] if "priority_rank" in rec.columns else []),
             *(["recommendation_status"] if "recommendation_status" in rec.columns else [])]],
        on="account_no", how="left", validate="one_to_one",
    )
    if profiles["recommended_action"].isna().any() or profiles["customer_type"].isna().any():
        raise ValueError("Some customer profiles could not be joined; regenerate Steps 13–15")
    # STEP 7: Attach latest recorded suspension status
    suspension = sheets["Suspension_History"].copy()

    suspension["suspension_dtm"] = pd.to_datetime(
        suspension["suspension_dtm"], errors="coerce"
    )

    suspension["reconnection_dtm"] = pd.to_datetime(
        suspension["reconnection_dtm"], errors="coerce"
    )

    latest_suspension = (
        suspension.sort_values(
            ["account_no", "suspension_dtm", "reconnection_dtm"],
            kind="stable"
        )
        .drop_duplicates("account_no", keep="last")
        .copy()
    )

    latest_status = (
        latest_suspension["status"]
        .astype("string")
        .str.strip()
        .str.casefold()
    )

    latest_suspension["service_status"] = latest_status.map({
        "still suspended": "Suspended",
        "reconnected after payment": "Reconnected"
    }).fillna("Unknown")

    profiles = profiles.merge(
        latest_suspension[["account_no", "service_status"]],
        on="account_no",
        how="left",
        validate="one_to_one"
    )

    profiles["service_status"] = (
        profiles["service_status"].fillna("Unknown")
    )

    # Derive current overdue strictly from payment/arrears fields. This is NOT
    # the company's formal default classification.
    for col in ("current_outstanding_LKR", "current_overdue_days", "unpaid_past_due_bills", "payment_reliability_score"):
        profiles[col] = pd.to_numeric(profiles[col], errors="coerce")
    profiles["is_overdue"] = (
        profiles["unpaid_past_due_bills"].gt(0) &
        profiles["current_overdue_days"].gt(0) &
        profiles["current_outstanding_LKR"].gt(0)
    )
    profiles = profiles.sort_values("account_no").reset_index(drop=True)
    return {"profiles": profiles, "sheets": sheets}


def context():
    missing = required_missing()
    if missing:
        return None, missing, None
    try:
        signatures = tuple((p.stat().st_size, p.stat().st_mtime_ns) for p in (WORKBOOK, SCORES, RECOMMENDATIONS))
        return load_data(signatures), [], None
    except Exception as exc:
        return None, [], str(exc)


def safe_rows(frame: pd.DataFrame, columns: list[str], maximum: int = 1000) -> list[dict]:
    cols = [c for c in columns if c in frame.columns]
    return frame.loc[:, cols].head(maximum).to_dict("records")


# >>> STEP 18 STAFF AUTH >>>
from staff_auth import configure_staff_auth as _step18_configure_staff_auth
_step18_configure_staff_auth(app)
# <<< STEP 18 STAFF AUTH <<<\n
# ==========================================================
# CREDIT SCORE & RANK - PRIVATE LOCAL SAMPLE
# Separate from synthetic Riskora creditworthiness scoring.
# ==========================================================

@app.route("/credit-data")
def credit_data():
    private = ROOT / "data" / "private_credit"

    ba = pd.read_csv(
        private / "CCW_BA_SEPTEMBER_2026_masked_sample.csv",
        dtype="string"
    ).fillna("")

    cr = pd.read_csv(
        private / "CCW_CR_SEPTEMBER_2026_masked_sample.csv",
        dtype="string"
    ).fillna("")

    required_ba = {
        "cr_number", "account_number",
        "credit_score", "credit_rank"
    }
    required_cr = {
        "cr_number", "min_credit_score", "max_credit_rank"
    }

    if not required_ba.issubset(ba.columns) or not required_cr.issubset(cr.columns):
        return "Credit data columns are incomplete.", 503

    query = request.args.get("q", "").strip()[:60]
    ba_results = []
    cr_results = []

    if query:
        key = query.upper()

        selected_ba = ba[
            ba["account_number"].str.upper().eq(key)
            | ba["cr_number"].str.upper().eq(key)
        ]

        ba_results = selected_ba.head(20).to_dict("records")

        refs = set(selected_ba["cr_number"].tolist())
        refs.add(key)

        selected_cr = cr[
            cr["cr_number"].str.upper().isin(refs)
        ]

        cr_results = selected_cr.head(20).to_dict("records")

    matching_refs = len(
        set(ba["cr_number"]) & set(cr["cr_number"])
    )

    score_values = pd.to_numeric(
        cr["min_credit_score"], errors="coerce"
    )
    special_codes = int(
        (
            score_values.gt(100)
            | cr["max_credit_rank"].eq("99")
        ).sum()
    )


    # Paginate both supplied credit datasets.
    # Show only 25 rows at a time.
    page_size = 25

    def get_page(frame, parameter):
        try:
            number = int(request.args.get(parameter, "1"))
        except (TypeError, ValueError):
            number = 1

        pages = max(1, (len(frame) + page_size - 1) // page_size)
        number = max(1, min(number, pages))
        start = (number - 1) * page_size

        return (
            frame.iloc[start:start + page_size].to_dict("records"),
            number,
            pages
        )

    directory_ba, ba_page, ba_pages = get_page(ba, "ba_page")
    directory_cr, cr_page, cr_pages = get_page(cr, "cr_page")

    return render_template(
        "credit_data.html",
        active="credit_data",
        q=query,
        ba_count=len(ba),
        cr_count=len(cr),
        matching_refs=matching_refs,
        special_codes=special_codes,
        ba_results=ba_results,
        cr_results=cr_results,
        directory_ba=directory_ba,
        directory_cr=directory_cr,
        ba_page=ba_page,
        cr_page=cr_page,
        ba_pages=ba_pages,
        cr_pages=cr_pages
    )


@app.before_request
def check_url_size():
    if len(request.query_string) > 600:
        abort(400)


@app.route("/")
def index():
    from historical_billing_dashboard import historical_context
    historical = historical_context(ROOT)

    state, missing, err = context()
    if state is None:
        return render_template("setup.html", missing=missing, error=err), 503
    prof = state["profiles"]
    counts = prof["recommended_action"].value_counts().to_dict()
    categories = prof["behaviour_classification"].value_counts().to_dict()
    overdue = prof[prof["is_overdue"]]
    return render_template(
        "index.html", active="overview", total=len(prof), overdue=len(overdue),
        overdue_total=float(overdue["current_outstanding_LKR"].sum()),
        avg_score=float(prof["payment_reliability_score"].mean()),
        collection_counts=counts, categories=categories,
        priority=safe_rows(prof[prof["recommended_action"].eq("Priority follow-up")]
                           .sort_values("current_overdue_days", ascending=False),
                           ["account_no", "customer_id", "customer_type", "current_overdue_days",
                            "current_outstanding_LKR", "recommended_action"], 8),
    )


@app.route("/customers")
def customers():
    state, missing, err = context()
    if state is None:
        return render_template("setup.html", missing=missing, error=err), 503
    q = request.args.get("q", "").strip()[:80]
    group = request.args.get("group", "")
    prof = state["profiles"]
    if q:
        prof = prof[prof["account_no"].str.contains(q, case=False, regex=False, na=False) |
                    prof["customer_id"].str.contains(q, case=False, regex=False, na=False)]
    groups = sorted(state["profiles"]["behaviour_classification"].dropna().unique().tolist())
    if group in groups:
        prof = prof[prof["behaviour_classification"].eq(group)]
    rows = safe_rows(prof, ["account_no", "customer_id", "customer_type", "service_type",
                            "behaviour_classification", "payment_reliability_score", "reliability_band",
                            "current_outstanding_LKR", "current_overdue_days", "is_overdue", "recommended_action"], 200)
    return render_template("customers.html", active="customers", rows=rows,
                           q=q, group=group, groups=groups, count=len(prof))


@app.route("/customers/<account_no>")
def customer_profile(account_no: str):
    if not account_no.isdigit() or len(account_no) > 18:
        abort(404)
    state, missing, err = context()
    if state is None:
        return render_template("setup.html", missing=missing, error=err), 503
    found = state["profiles"][state["profiles"]["account_no"].eq(account_no)]
    if found.empty:
        abort(404)
    user = found.iloc[0].to_dict()
    sh = state["sheets"]
    billing = sh["Billing"].loc[sh["Billing"]["account_no"].eq(account_no)].copy()
    payments = sh["Payments"].loc[sh["Payments"]["account_no"].eq(account_no)].copy()
    collections = sh["Collection_History"].loc[sh["Collection_History"]["account_no"].eq(account_no)].copy()
    suspension = sh["Suspension_History"].loc[sh["Suspension_History"]["account_no"].eq(account_no)].copy()
    for df, col in ((billing, "bill_dtm"), (payments, "payment_dtm"), (collections, "action_dtm"), (suspension, "suspension_dtm")):
        if col in df.columns:
            df.sort_values(col, ascending=False, inplace=True)
    scores = [
        ("Bills settled", "points_settlement", 25),
        ("Payment consistency", "points_consistency", 20),
        ("Payment delay", "points_delay", 15),
        ("Current arrears", "points_current_arrears", 25),
        ("Outstanding exposure", "points_balance_exposure", 10),
        ("Collection history", "points_collection_history", 5),
    ]
    breakdown = [{"label": name, "earned": user.get(key, 0), "max": limit} for name, key, limit in scores]
    return render_template(
        "profile.html", active="customers", c=user, breakdown=breakdown,
        billing=safe_rows(billing, ["invoice_no", "bill_dtm", "payment_due_dtm", "bill_settled_dtm", "invoice_net_amt", "invoice_tax_amt", "balance_out_amt"], 80),
        payments=safe_rows(payments, ["payment_id", "invoice_no", "payment_dtm", "payment_amt", "payment_method"], 100),
        collections=safe_rows(collections, ["action_dtm", "action_type", "days_past_due", "outcome"], 100),
        suspensions=safe_rows(suspension, ["suspension_dtm", "reconnection_dtm", "reason", "status"], 100),
    )


@app.route("/creditworthiness")
def creditworthiness():
    state, missing, err = context()
    if state is None:
        return render_template("setup.html", missing=missing, error=err), 503
    prof = state["profiles"]
    band_counts = prof["reliability_band"].value_counts().to_dict()
    return render_template("creditworthiness.html", active="creditworthiness", bands=band_counts,
                           scores=safe_rows(prof.sort_values("payment_reliability_score", ascending=True),
                                            ["account_no", "customer_id", "behaviour_classification", "reliability_band",
                                             "payment_reliability_score", "current_overdue_days"], 200))


@app.route("/collections")
def collections():
    state, missing, err = context()
    if state is None:
        return render_template("setup.html", missing=missing, error=err), 503
    action = request.args.get("action", "")
    prof = state["profiles"]
    allowed = ["No follow-up", "Normal follow-up", "Priority follow-up"]
    if action in allowed:
        prof = prof[prof["recommended_action"].eq(action)]
    prof = prof.sort_values(["current_overdue_days", "current_outstanding_LKR"], ascending=False)
    return render_template("collections.html", active="collections", action=action,
                           count=len(prof), rows=safe_rows(prof, ["account_no", "customer_id", "customer_type",
                                       "behaviour_classification", "recommended_action", "recommendation_reason",
                                       "current_overdue_days", "current_outstanding_LKR", "is_overdue"], 200))


@app.errorhandler(404)
def not_found(error):
    return render_template("not_found.html"), 404

# ==========================================================
# RISKORA - USAGE ANALYTICS
# Read-only analysis of local masked SLT usage sample
# ==========================================================

@app.route("/usage-analytics")
def usage_analytics():

    usage_file = (
        ROOT / "data" / "private_usage" / "usage_sample.csv"
    )

    if not usage_file.is_file():
        return (
            "Usage dataset not found. "
            "Check data/private_usage/usage_sample.csv",
            503
        )

    try:
        df = pd.read_csv(
            usage_file,
            dtype={
                "account_num": "string",
                "subscriber_id": "string",
                "offer_name": "string",
                "ymd": "string"
            }
        )
    except (OSError, ValueError, pd.errors.ParserError):
        return "Unable to read the local usage dataset.", 503

    required_columns = {
        "account_num",
        "subscriber_id",
        "offer_name",
        "ymd",
        "cc_total_octets_bytes",
        "cc_input_octets_bytes",
        "cc_output_octets_bytes"
    }

    missing_columns = required_columns - set(df.columns)

    if missing_columns:
        return (
            "Usage dataset is missing required columns: "
            + ", ".join(sorted(missing_columns)),
            503
        )

    # Count masked accounts and subscribers.
    account_count = df["account_num"].nunique()
    subscriber_count = df["subscriber_id"].nunique()

    # Preserve the distinction between usage-volume fields.
    def bytes_to_gib(column):
        values = pd.to_numeric(
            df[column],
            errors="coerce"
        )

        return float(values.sum()) / (1024 ** 3)

    input_gib = bytes_to_gib("cc_input_octets_bytes")
    output_gib = bytes_to_gib("cc_output_octets_bytes")
    total_gib = bytes_to_gib("cc_total_octets_bytes")

    # Read the dates represented in the sample.
    dates = pd.to_datetime(
        df["ymd"],
        format="%Y%m%d",
        errors="coerce"
    )

    valid_dates = dates.dropna()

    if valid_dates.empty:
        usage_date = "Unknown"
    elif valid_dates.nunique() == 1:
        usage_date = valid_dates.iloc[0].strftime(
            "%d %b %Y"
        )
    else:
        usage_date = (
            valid_dates.min().strftime("%d %b %Y")
            + " to "
            + valid_dates.max().strftime("%d %b %Y")
        )

    # Top 10 offers by record count.
    offers = (
        df["offer_name"]
        .fillna("Unknown")
        .str.strip()
        .replace("", "Unknown")
    )

    offer_counts = offers.value_counts().head(10)

    top_offers = [
        {
            "name": str(name),
            "records": int(count)
        }
        for name, count in offer_counts.items()
    ]

    usage = {
        "records": int(len(df)),
        "accounts": int(account_count),
        "subscribers": int(subscriber_count),
        "offers": int(df["offer_name"].nunique()),
        "input_gib": input_gib,
        "output_gib": output_gib,
        "total_gib": total_gib,
        "usage_date": usage_date,
        "top_offers": top_offers
    }

    return render_template(
        "usage_analytics.html",
        active="usage",
        usage=usage
    )


# ==========================================================
# RISKORA - ONE-MONTH NON-PAYMENT PREDICTION
# Synthetic demonstration only
# ==========================================================

@app.route("/risk-prediction")
def risk_prediction():

    prediction_file = (
        OUTPUT_DIR / "riskora_next_month_predictions.csv"
    )

    if not prediction_file.exists():
        return (
            "Prediction results not found. "
            "Please run 43_train_riskora_next_month.py.",
            503
        )

    df = pd.read_csv(
        prediction_file,
        dtype={
            "account_no": "string",
            "customer_id": "string"
        }
    )

    required_columns = {
        "account_no",
        "customer_id",
        "predicted_risk",
        "risk_band",
        "forecast_month"
    }

    if not required_columns.issubset(df.columns):
        return "Prediction CSV has missing columns.", 503

    df["predicted_risk"] = pd.to_numeric(
        df["predicted_risk"],
        errors="coerce"
    )

    df = df.dropna(subset=["predicted_risk"])

    df = df[
        df["predicted_risk"].between(0, 1)
    ].copy()

    df = df.sort_values(
        "predicted_risk",
        ascending=False
    )

    total = len(df)

    high = int(
        (df["risk_band"] == "High (demo)").sum()
    )

    medium = int(
        (df["risk_band"] == "Medium (demo)").sum()
    )

    low = int(
        (df["risk_band"] == "Low (demo)").sum()
    )

    forecast_month = (
        str(df["forecast_month"].iloc[0])
        if total > 0 else "N/A"
    )

    predictions = df.to_dict(
        orient="records"
    )

    return render_template(
        "risk_prediction.html",
        active="risk_prediction",
        total=total,
        high=high,
        medium=medium,
        low=low,
        forecast_month=forecast_month,
        predictions=predictions
    )


# >>> STEP 41 MODEL VALIDATION DASHBOARD >>>
@app.route('/validation-status', methods=['GET'])
def validation_status():
    # Only a small whitelist of aggregated progress data is read. Never query raw customer records.
    from validation_dashboard import dashboard_context as _step41_dashboard_context
    return render_template('validation_status.html', active='validation',
                           **_step41_dashboard_context(ROOT))
# <<< STEP 41 MODEL VALIDATION DASHBOARD <<<

# >>> STEP 42 HISTORICAL BILLING DASHBOARD >>>
@app.route('/historical-billing', methods=['GET'])
def historical_billing():
    # Aggregated Step 29 CSVs only; analyst/admin staff access enforced by staff_auth.
    from historical_billing_dashboard import historical_context as _step42_historical_context
    return render_template('historical_billing.html', active='historical',
                           **_step42_historical_context(ROOT, request.args.get('year', '')))
# <<< STEP 42 HISTORICAL BILLING DASHBOARD <<<

if __name__ == "__main__":
    # Local-only development server; intentionally not accessible from internet/LAN.
    app.run(host="127.0.0.1", port=5000, debug=False)
