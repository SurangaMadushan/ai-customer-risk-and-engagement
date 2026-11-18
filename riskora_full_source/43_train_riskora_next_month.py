
from pathlib import Path
import json

import numpy as np
import pandas as pd

# RISKORA - ONE-MONTH NON-PAYMENT PREDICTION
# Synthetic demonstration data only.
# No scikit-learn required.

ROOT = Path(__file__).resolve().parent

SOURCE = (
    ROOT / "data" /
    "SLT_Payment_Behaviour_Dummy_Data.xlsx"
)

OUTPUT = ROOT / "outputs"

billing = pd.read_excel(
    SOURCE,
    sheet_name="Billing",
    dtype={"account_no": "string"}
)

master = pd.read_excel(
    SOURCE,
    sheet_name="Customer_Master",
    dtype={"account_no": "string"}
)

for column in [
    "bill_dtm",
    "payment_due_dtm",
    "bill_settled_dtm"
]:
    billing[column] = pd.to_datetime(
        billing[column],
        errors="coerce"
    )

billing["account_no"] = billing["account_no"].str.strip()

billing["invoice_net_amt"] = pd.to_numeric(
    billing["invoice_net_amt"],
    errors="coerce"
)

billing = billing.dropna(
    subset=[
        "account_no",
        "bill_dtm",
        "payment_due_dtm"
    ]
)

billing["due_month"] = (
    billing["payment_due_dtm"].dt.to_period("M")
)

FEATURES = [
    "past_invoice_count",
    "settled_by_cutoff",
    "unsettled_by_cutoff",
    "on_time_share",
    "mean_settlement_delay_days",
    "recent_due_invoices",
    "recent_unsettled",
    "mean_invoice_amount",
    "historical_billing_months"
]


def make_features(history, cutoff):

    # Only use information available at the cutoff.
    past = history.loc[
        (history["bill_dtm"] <= cutoff) &
        (history["payment_due_dtm"] <= cutoff)
    ].copy()

    if past.empty:
        return None

    settled = (
        past["bill_settled_dtm"].notna() &
        (past["bill_settled_dtm"] <= cutoff)
    )

    completed = past.loc[settled]

    delay = (
        completed["bill_settled_dtm"] -
        completed["payment_due_dtm"]
    ).dt.total_seconds() / 86400

    recent = (
        past["payment_due_dtm"] >
        cutoff - pd.Timedelta(days=90)
    )

    return {
        "past_invoice_count": float(len(past)),
        "settled_by_cutoff": float(settled.sum()),
        "unsettled_by_cutoff": float((~settled).sum()),
        "on_time_share": (
            float((delay <= 0).mean())
            if len(delay) else 0.0
        ),
        "mean_settlement_delay_days": (
            float(delay.mean()) if len(delay) else 0.0
        ),
        "recent_due_invoices": float(recent.sum()),
        "recent_unsettled": float(
            (recent & ~settled).sum()
        ),
        "mean_invoice_amount": float(
            past["invoice_net_amt"].mean()
        ),
        "historical_billing_months": float(
            past["bill_dtm"].dt.to_period("M").nunique()
        )
    }


# Prepare historical customer-month training samples.

account_history = {
    str(account): records.copy()
    for account, records
    in billing.groupby("account_no")
}

rows = []

for account, history in account_history.items():

    for month, due in history.groupby("due_month"):

        cutoff = (
            month.to_timestamp() -
            pd.Timedelta(seconds=1)
        )

        features = make_features(history, cutoff)

        if features is None:
            continue

        month_end = (
            (month + 1).to_timestamp() -
            pd.Timedelta(seconds=1)
        )

        unsettled = (
            due["bill_settled_dtm"].isna() |
            (due["bill_settled_dtm"] > month_end)
        )

        rows.append({
            "account_no": account,
            "target_month": str(month),
            "unsettled_next_month": int(
                unsettled.any()
            ),
            **features
        })


samples = pd.DataFrame(rows)

if samples.empty:
    raise SystemExit("No training samples available.")

samples = samples.sort_values(
    ["target_month", "account_no"]
)

months = sorted(
    samples["target_month"].unique()
)

if len(months) < 5:
    raise SystemExit(
        "Insufficient historical months."
    )

# Time-based testing: hold out the last two due months.

test_months = months[-2:]

train = samples[
    ~samples["target_month"].isin(test_months)
].copy()

test = samples[
    samples["target_month"].isin(test_months)
].copy()

x_train = train[FEATURES].to_numpy(dtype=float)
x_test = test[FEATURES].to_numpy(dtype=float)

fill = np.nanmedian(
    np.where(
        np.isfinite(x_train),
        x_train,
        np.nan
    ),
    axis=0
)

fill = np.where(
    np.isfinite(fill),
    fill,
    0.0
)

x_train = np.where(
    np.isfinite(x_train),
    x_train,
    fill
)

x_test = np.where(
    np.isfinite(x_test),
    x_test,
    fill
)

# Scale features using training data only.

mean = x_train.mean(axis=0)
std = x_train.std(axis=0)

std[std < 1e-9] = 1.0

x_train = (x_train - mean) / std
x_test = (x_test - mean) / std

X = np.column_stack([
    np.ones(len(x_train)),
    x_train
])

Xt = np.column_stack([
    np.ones(len(x_test)),
    x_test
])

y = train[
    "unsettled_next_month"
].to_numpy(dtype=float)

y_test = test[
    "unsettled_next_month"
].to_numpy(dtype=int)

if len(np.unique(y)) < 2:
    raise SystemExit(
        "Both outcome classes are required."
    )


# NumPy logistic regression with L2 regularization.
# No scikit-learn or np.linalg dependency.

def sigmoid(z):
    return 1.0 / (
        1.0 + np.exp(-np.clip(z, -35, 35))
    )


beta = np.zeros(X.shape[1])

for _ in range(1800):

    probabilities = sigmoid(X @ beta)

    gradient = (
        X.T @ (probabilities - y)
    ) / len(y)

    gradient[1:] += 0.02 * beta[1:]

    beta -= 0.10 * gradient


# Evaluate using held-out future months.

test_scores = sigmoid(Xt @ beta)

predicted_labels = (
    test_scores >= 0.5
).astype(int)

accuracy = float(
    np.mean(predicted_labels == y_test)
)


def average_precision(actual, scores):

    order = np.argsort(
        -scores,
        kind="stable"
    )

    ranked = actual[order]
    positives = ranked.sum()

    if not positives:
        return None

    precision = (
        np.cumsum(ranked) /
        np.arange(1, len(ranked) + 1)
    )

    return float(
        (precision * ranked).sum() /
        positives
    )


def roc_auc(actual, scores):

    positives = int(actual.sum())
    negatives = int(len(actual) - positives)

    if not positives or not negatives:
        return None

    ranks = pd.Series(scores).rank(
        method="average"
    ).to_numpy(dtype=float)

    return float(
        (
            ranks[actual == 1].sum() -
            positives * (positives + 1) / 2
        ) /
        (positives * negatives)
    )


ap = average_precision(
    y_test, test_scores
)

auc = roc_auc(
    y_test, test_scores
)

metrics = {
    "data_type": "synthetic_demo_only",
    "model_type": "numpy_l2_logistic_regression",
    "target_definition": (
        "Conditional on at least one invoice "
        "due next month, at least one remains "
        "unsettled at that month-end"
    ),
    "train_samples": int(len(train)),
    "test_samples": int(len(test)),
    "test_months": list(test_months),
    "accuracy_at_0_5": round(accuracy, 4),
    "average_precision": (
        round(ap, 4) if ap is not None else None
    ),
    "roc_auc": (
        round(auc, 4) if auc is not None else None
    ),
    "warning": (
        "Synthetic data only. Risk scores are "
        "not calibrated or validated for real SLT customers."
    )
}


# Forecast the next calendar month.

latest_month = max(
    billing["due_month"].dropna().unique()
)

forecast_month = latest_month + 1

as_of = (
    forecast_month.to_timestamp() -
    pd.Timedelta(seconds=1)
)

forecast_rows = []

for account, history in account_history.items():

    features = make_features(
        history,
        as_of
    )

    if features is not None:
        forecast_rows.append({
            "account_no": account,
            **features
        })

forecast = pd.DataFrame(forecast_rows)

raw = forecast[FEATURES].to_numpy(
    dtype=float
)

raw = np.where(
    np.isfinite(raw),
    raw,
    fill
)

Xf = np.column_stack([
    np.ones(len(raw)),
    (raw - mean) / std
])

forecast["predicted_risk"] = sigmoid(
    Xf @ beta
)

# Demonstration-only risk bands.

forecast["risk_band"] = pd.cut(
    forecast["predicted_risk"],
    bins=[-0.01, 0.40, 0.70, 1.01],
    labels=[
        "Low (demo)",
        "Medium (demo)",
        "High (demo)"
    ]
).astype(str)

master["account_no"] = (
    master["account_no"].str.strip()
)

forecast = forecast.merge(
    master[[
        "account_no",
        "customer_id"
    ]],
    on="account_no",
    how="left",
    validate="one_to_one"
)

forecast["as_of_date"] = str(
    as_of.date()
)

forecast["forecast_month"] = str(
    forecast_month
)


# Save only synthetic prediction outputs.

OUTPUT.mkdir(
    parents=True,
    exist_ok=True
)

forecast[[
    "account_no",
    "customer_id",
    "as_of_date",
    "forecast_month",
    "predicted_risk",
    "risk_band"
]].sort_values(
    "predicted_risk",
    ascending=False
).to_csv(
    OUTPUT / "riskora_next_month_predictions.csv",
    index=False
)

(
    OUTPUT /
    "riskora_next_month_model_metrics.json"
).write_text(
    json.dumps(metrics, indent=2),
    encoding="utf-8"
)


print("\nRISKORA NEXT-MONTH PREDICTION")
print("--------------------------------")

print("Dataset: SYNTHETIC DEMO ONLY")
print("Model: NumPy Logistic Regression")

print("Training samples:", len(train))
print("Test samples:", len(test))
print("Held-out months:", test_months)

print("Accuracy:", metrics["accuracy_at_0_5"])
print("Average precision:", metrics["average_precision"])
print("ROC-AUC:", metrics["roc_auc"])

print("Prediction cutoff:", as_of.date())
print("Forecast month:", forecast_month)
print("Customers predicted:", len(forecast))

print("\nFILES SAVED:")
print("outputs/riskora_next_month_predictions.csv")
print("outputs/riskora_next_month_model_metrics.json")

print("\nPrediction training completed.")
