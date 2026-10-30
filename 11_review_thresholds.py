from pathlib import Path
import joblib
import pandas as pd

from sklearn.base import clone
from sklearn.metrics import precision_score, recall_score, f1_score

project_folder = Path(__file__).resolve().parent
output_folder = project_folder / "outputs"
model_folder = project_folder / "models"

data = pd.read_csv(
    output_folder / "training_data.csv",
    parse_dates=["invoice_date", "label_date"]
)

TARGET = "unpaid_after_30_days"
VALIDATION_START = pd.Timestamp("2025-08-29")
TEST_START = pd.Timestamp("2025-10-28")

features = [
    "invoice_amount",
    "payment_terms_days",
    "history_invoice_count",
    "total_outstanding",
    "total_overdue_amount",
    "overdue_invoice_count",
    "max_overdue_days",
    "paid_invoice_count",
    "average_payment_delay",
    "late_payment_ratio",
    "has_paid_history",
    "customer_type",
    "service_type",
    "package",
]

train = data[
    (data["invoice_date"] < VALIDATION_START)
    & (data["label_date"] < VALIDATION_START)
].copy()

validation = data[
    (data["invoice_date"] >= VALIDATION_START)
    & (data["invoice_date"] < TEST_START)
    & (data["label_date"] < TEST_START)
].copy()

# Clone removes the previous fitted state.
saved_pipeline = joblib.load(
    model_folder / "payment_risk_model.joblib"
)
validation_model = clone(saved_pipeline)

print("Training a copy for threshold review...", flush=True)
validation_model.fit(train[features], train[TARGET])

positive_index = list(validation_model.classes_).index(1)
probabilities = validation_model.predict_proba(
    validation[features]
)[:, positive_index]

actual = validation[TARGET].to_numpy()
results = []

for threshold in [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]:
    predicted = (probabilities >= threshold).astype(int)

    results.append({
        "threshold": threshold,
        "precision": precision_score(
            actual, predicted, zero_division=0
        ),
        "recall": recall_score(
            actual, predicted, zero_division=0
        ),
        "f1": f1_score(actual, predicted, zero_division=0),
        "flagged_invoices": int(predicted.sum()),
        "false_alarms": int(
            ((actual == 0) & (predicted == 1)).sum()
        ),
        "missed_unpaid": int(
            ((actual == 1) & (predicted == 0)).sum()
        ),
    })

table = pd.DataFrame(results)

table.to_csv(
    output_folder / "threshold_review.csv",
    index=False
)

print("\nVALIDATION THRESHOLD COMPARISON")
print(table.round(4).to_string(index=False))

best = table.sort_values(
    ["f1", "recall"],
    ascending=False
).iloc[0]

print(
    "\nHighest validation F1 among these thresholds:",
    best["threshold"]
)
print("This is a proposal for business review.")
print("Saved: outputs/threshold_review.csv")