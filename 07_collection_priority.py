from pathlib import Path
import pandas as pd

project_folder = Path(__file__).resolve().parent
output_folder = project_folder / "outputs"

data = pd.read_csv(
    output_folder / "customer_features_with_behaviour.csv"
)

# DRAFT configuration — confirm with the team.
DAYS_CAP = 90
AMOUNT_CAP = 50000
INVOICE_CAP = 6

HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 40

data["days_points"] = (
    data["max_overdue_days"] / DAYS_CAP
).clip(0, 1) * 40

data["amount_points"] = (
    data["total_overdue_amount"] / AMOUNT_CAP
).clip(0, 1) * 30

data["invoice_points"] = (
    data["overdue_invoice_count"] / INVOICE_CAP
).clip(0, 1) * 20

data["behaviour_points"] = (
    data["late_payment_ratio"].clip(0, 1) * 10
)

data["behaviour_note"] = "Observed paid-invoice history"
data.loc[
    data["has_paid_history"] == 0, "behaviour_note"
] = "Unknown: no paid-invoice history"

# Draft policy: unknown behaviour adds no points.
# Keep the note so users can see the missing evidence.
data["behaviour_points"] = data["behaviour_points"].fillna(0)

data["priority_score"] = data[
    ["days_points", "amount_points",
     "invoice_points", "behaviour_points"]
].sum(axis=1)

eligible = data["total_overdue_amount"] > 0

# Customers with no overdue balance are excluded from collection.
data.loc[~eligible, "priority_score"] = 0

data["priority_category"] = "No collection required"
data.loc[eligible, "priority_category"] = "Low"

data.loc[
    eligible & (data["priority_score"] >= MEDIUM_THRESHOLD),
    "priority_category"
] = "Medium"

data.loc[
    eligible & (data["priority_score"] >= HIGH_THRESHOLD),
    "priority_category"
] = "High"

# Deterministic ranking: resolve equal scores consistently.
ranked = data.loc[eligible].sort_values(
    [
        "priority_score",
        "max_overdue_days",
        "total_overdue_amount",
        "customer_id"
    ],
    ascending=[False, False, False, True]
).copy()

ranked["collection_rank"] = range(1, len(ranked) + 1)

data = data.merge(
    ranked[["customer_id", "collection_rank"]],
    on="customer_id",
    how="left",
    validate="one_to_one"
)

data["collection_rank"] = data["collection_rank"].astype("Int64")

for column in [
    "days_points", "amount_points",
    "invoice_points", "behaviour_points", "priority_score"
]:
    data[column] = data[column].round(2)
    ranked[column] = ranked[column].round(2)

data.to_csv(
    output_folder / "customer_priority_scores.csv",
    index=False
)

ranked.to_csv(
    output_folder / "collection_call_list.csv",
    index=False
)

print("DRAFT COLLECTION PRIORITY RESULTS\n")
print(data["priority_category"].value_counts().to_string())

print("\nTop five customers to contact:")
print(
    ranked[
        [
            "customer_id",
            "total_overdue_amount",
            "max_overdue_days",
            "priority_score",
            "priority_category",
            "collection_rank"
        ]
    ].head().to_string(index=False)
)

print("\nSaved:")
print("outputs/customer_priority_scores.csv")
print("outputs/collection_call_list.csv")