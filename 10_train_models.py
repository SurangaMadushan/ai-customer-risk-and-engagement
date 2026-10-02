from pathlib import Path
import json

import joblib
import pandas as pd

from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    average_precision_score,
    classification_report,
    confusion_matrix,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier


# 1. Set file locations.
project_folder = Path(__file__).resolve().parent
output_folder = project_folder / "outputs"
model_folder = project_folder / "models"
model_folder.mkdir(exist_ok=True)

data = pd.read_csv(
    output_folder / "training_data.csv",
    parse_dates=["invoice_date", "label_date"],
)

TARGET = "unpaid_after_30_days"

# Explicitly select inputs. IDs and future outcomes are excluded.
numeric_features = [
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
]

categorical_features = [
    "customer_type",
    "service_type",
    "package",
]

features = numeric_features + categorical_features

if data["invoice_id"].duplicated().any():
    raise ValueError("Duplicate invoice IDs in training data.")

if data[TARGET].isna().any() or not data[TARGET].isin([0, 1]).all():
    raise ValueError("Target must contain only 0 and 1.")

# 2. Split by time.
VALIDATION_START = pd.Timestamp("2025-08-29")
TEST_START = pd.Timestamp("2025-10-28")

# Only include outcomes already known before validation starts.
train = data[
    (data["invoice_date"] < VALIDATION_START)
    & (data["label_date"] < VALIDATION_START)
].copy()

# Validation outcomes must be known before testing starts.
validation = data[
    (data["invoice_date"] >= VALIDATION_START)
    & (data["invoice_date"] < TEST_START)
    & (data["label_date"] < TEST_START)
].copy()

test = data[
    data["invoice_date"] >= TEST_START
].copy()

for name, frame in [
    ("Training", train),
    ("Validation", validation),
    ("Testing", test),
]:
    if frame.empty or frame[TARGET].nunique() != 2:
        raise ValueError(
            f"{name} must contain examples of both target classes."
        )

    print(
        f"{name}: {len(frame):,} rows | "
        f"unpaid rate: {frame[TARGET].mean():.1%}",
        flush=True,
    )

# 3. Prepare numeric and categorical inputs.
numeric_processing = Pipeline([
    ("imputer", SimpleImputer(
        strategy="median",
        add_indicator=True,
    )),
])

categorical_processing = Pipeline([
    ("imputer", SimpleImputer(strategy="most_frequent")),
    ("encoder", OneHotEncoder(handle_unknown="ignore")),
])

preprocessing = ColumnTransformer([
    ("numeric", numeric_processing, numeric_features),
    ("categorical", categorical_processing, categorical_features),
])

negative_count = int(train[TARGET].eq(0).sum())
positive_count = int(train[TARGET].eq(1).sum())
weight_ratio = negative_count / positive_count

# 4. Define models.
models = {
    "Baseline": DummyClassifier(strategy="most_frequent"),

    "RandomForest": RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_leaf=10,
        class_weight="balanced",
        random_state=42,
        n_jobs=2,
    ),

    "XGBoost": XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=weight_ratio,
        eval_metric="logloss",
        tree_method="hist",
        random_state=42,
        n_jobs=2,
    ),
}


def evaluate(pipeline, frame):
    predictions = pipeline.predict(frame[features])
    positive_index = list(pipeline.classes_).index(1)

    probabilities = pipeline.predict_proba(
        frame[features]
    )[:, positive_index]

    metrics = {
        "accuracy": accuracy_score(frame[TARGET], predictions),
        "precision": precision_score(
            frame[TARGET], predictions, zero_division=0
        ),
        "recall": recall_score(
            frame[TARGET], predictions, zero_division=0
        ),
        "f1": f1_score(
            frame[TARGET], predictions, zero_division=0
        ),
        "average_precision": average_precision_score(
            frame[TARGET], probabilities
        ),
    }

    return metrics, predictions, probabilities


# 5. Train and compare using validation data.
pipelines = {}
validation_results = []

for name, model in models.items():
    print(f"\nTraining {name}...", flush=True)

    pipeline = Pipeline([
        ("preprocessing", clone(preprocessing)),
        ("model", model),
    ])

    pipeline.fit(train[features], train[TARGET])
    metrics, _, _ = evaluate(pipeline, validation)

    validation_results.append({"model": name, **metrics})
    pipelines[name] = pipeline

results = pd.DataFrame(validation_results)

print("\nVALIDATION RESULTS")
print(results.round(4).to_string(index=False))

results.to_csv(
    output_folder / "model_validation_results.csv",
    index=False,
)

# Select using validation F1; resolve ties with average precision.
candidates = results[
    results["model"].isin(["RandomForest", "XGBoost"])
]

best_name = candidates.sort_values(
    ["f1", "average_precision"],
    ascending=False,
).iloc[0]["model"]

print("\nSelected model:", best_name, flush=True)

# 6. Refit selected model using outcomes known before test starts.
refit_data = data[
    (data["invoice_date"] < TEST_START)
    & (data["label_date"] < TEST_START)
].copy()

best_pipeline = clone(pipelines[best_name])

if best_name == "XGBoost":
    refit_ratio = (
        refit_data[TARGET].eq(0).sum()
        / refit_data[TARGET].eq(1).sum()
    )

    best_pipeline.set_params(
        model__scale_pos_weight=float(refit_ratio)
    )

best_pipeline.fit(
    refit_data[features],
    refit_data[TARGET],
)

# 7. Evaluate selected model on the final test period.
test_metrics, test_predictions, test_probabilities = evaluate(
    best_pipeline, test
)

print("\nFINAL TEST RESULTS")
print(classification_report(
    test[TARGET],
    test_predictions,
    labels=[0, 1],
    target_names=["Paid by deadline", "Unpaid at deadline"],
    zero_division=0,
    digits=4,
))

matrix = confusion_matrix(
    test[TARGET],
    test_predictions,
    labels=[0, 1],
)

print("Confusion matrix: rows = actual, columns = predicted")
print(matrix)

pd.DataFrame([
    {"model": best_name, **test_metrics}
]).to_csv(
    output_folder / "model_test_results.csv",
    index=False,
)

prediction_output = test[
    ["invoice_id", "customer_id", "invoice_date", TARGET]
].copy()

prediction_output["predicted_unpaid"] = test_predictions
prediction_output["unpaid_probability_estimate"] = (
    test_probabilities
)

prediction_output.to_csv(
    output_folder / "test_predictions.csv",
    index=False,
)

# 8. Save model together with its preprocessing.
joblib.dump(
    best_pipeline,
    model_folder / "payment_risk_model.joblib",
)

metadata = {
    "selected_model": best_name,
    "target": TARGET,
    "target_definition": (
        "Invoice has an unpaid balance greater than 0.01 "
        "30 days after its due date."
    ),
    "prediction_time": "Start of invoice issue date",
    "features": features,
    "validation_start": str(VALIDATION_START.date()),
    "test_start": str(TEST_START.date()),
    "classification_threshold": 0.5,
    "final_training_rows": len(refit_data),
    "test_rows": len(test),
    "synthetic_data": True,
}

with open(
    model_folder / "model_metadata.json",
    "w",
    encoding="utf-8",
) as file:
    json.dump(metadata, file, indent=2)

print("\nSaved model: models/payment_risk_model.joblib")
print("Saved metadata: models/model_metadata.json")
print("Saved evaluation files in outputs.")