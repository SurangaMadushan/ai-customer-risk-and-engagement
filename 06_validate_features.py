from pathlib import Path
import pandas as pd

project_folder = Path(__file__).resolve().parent

data = pd.read_csv(
    project_folder / "outputs"
    / "customer_features_with_behaviour.csv"
)

has_history = data["paid_invoice_count"] > 0
no_history = ~has_history

checks = {
    "Missing customer IDs":
        int(data["customer_id"].isna().sum()),

    "Duplicate customer IDs":
        int(data["customer_id"].duplicated().sum()),

    "Negative outstanding amounts":
        int((data["total_outstanding"] < 0).sum()),

    "Negative overdue amounts":
        int((data["total_overdue_amount"] < 0).sum()),

    "Overdue amount exceeds outstanding":
        int((
            data["total_overdue_amount"]
            > data["total_outstanding"] + 0.01
        ).sum()),

    "Negative overdue days":
        int((data["max_overdue_days"] < 0).sum()),

    "Paid invoice count exceeds invoice count":
        int((
            data["paid_invoice_count"]
            > data["history_invoice_count"]
        ).sum()),

    "Late invoice count exceeds paid invoice count":
        int((
            data["late_paid_invoice_count"]
            > data["paid_invoice_count"]
        ).sum()),

    "Payment ratios outside 0 to 1":
        int((
            (data["late_payment_ratio"] < 0)
            | (data["late_payment_ratio"] > 1)
        ).sum()),

    "Negative average payment delays":
        int((data["average_payment_delay"] < 0).sum()),

    "Missing behaviour values despite paid history":
        int((
            has_history
            & data[
                ["average_payment_delay", "late_payment_ratio"]
            ].isna().any(axis=1)
        ).sum()),

    "Behaviour values present without paid history":
        int((
            no_history
            & data[
                ["average_payment_delay", "late_payment_ratio"]
            ].notna().any(axis=1)
        ).sum()),

    "Incorrect paid-history flag":
        int((
            data["has_paid_history"]
            != has_history.astype(int)
        ).sum()),
}

required_columns = [
    "history_invoice_count",
    "total_outstanding",
    "total_overdue_amount",
    "overdue_invoice_count",
    "max_overdue_days",
    "paid_invoice_count",
    "late_paid_invoice_count",
    "has_paid_history",
    "assessment_date",
]

checks["Missing required feature values"] = int(
    data[required_columns].isna().sum().sum()
)

# Check that the ratio matches its underlying counts.
expected_ratio = (
    data.loc[has_history, "late_paid_invoice_count"]
    / data.loc[has_history, "paid_invoice_count"]
)

checks["Ratios inconsistent with invoice counts"] = int(
    (
        data.loc[has_history, "late_payment_ratio"]
        - expected_ratio
    ).abs().gt(0.000051).sum()
)

print("FEATURE VALIDATION\n")

for name, count in checks.items():
    print(f"{name}: {count}")

print(
    "\nCustomers without paid history:",
    int(no_history.sum())
)
print("Their blank delay and ratio values are expected.")

if sum(checks.values()) == 0:
    print("\nPASS: All listed feature checks passed.")
else:
    print("\nREVIEW: Fix the flagged values before continuing.")