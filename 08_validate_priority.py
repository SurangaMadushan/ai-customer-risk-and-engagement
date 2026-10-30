from pathlib import Path
import pandas as pd

project_folder = Path(__file__).resolve().parent
output_folder = project_folder / "outputs"

customers = pd.read_csv(
    output_folder / "customer_priority_scores.csv"
)
call_list = pd.read_csv(
    output_folder / "collection_call_list.csv"
)

eligible = customers["total_overdue_amount"] > 0

checks = {
    "Duplicate customer IDs":
        int(customers["customer_id"].duplicated().sum()),

    "Missing scores":
        int(customers["priority_score"].isna().sum()),

    "Scores outside 0–100":
        int((
            (customers["priority_score"] < 0)
            | (customers["priority_score"] > 100)
        ).sum()),

    "Customers without overdue debt in call list":
        int((call_list["total_overdue_amount"] <= 0).sum()),

    "Duplicate customers in call list":
        int(call_list["customer_id"].duplicated().sum()),

    "Non-overdue customers with nonzero scores":
        int((
            ~eligible & (customers["priority_score"] != 0)
        ).sum()),
}

expected_ids = set(customers.loc[eligible, "customer_id"])
actual_ids = set(call_list["customer_id"])

checks["Missing eligible customers"] = len(
    expected_ids - actual_ids
)
checks["Unexpected customers in call list"] = len(
    actual_ids - expected_ids
)

# Check categories against the draft thresholds.
expected_category = pd.Series(
    "No collection required", index=customers.index
)
expected_category.loc[eligible] = "Low"
expected_category.loc[
    eligible & (customers["priority_score"] >= 40)
] = "Medium"
expected_category.loc[
    eligible & (customers["priority_score"] >= 70)
] = "High"

checks["Incorrect priority categories"] = int(
    (customers["priority_category"] != expected_category).sum()
)

# Scores should decrease down the call list.
checks["Scores increase down the ranking"] = int(
    call_list["priority_score"].diff().gt(0).sum()
)

expected_ranks = list(range(1, len(call_list) + 1))
checks["Ranks are not consecutive"] = int(
    call_list["collection_rank"].tolist() != expected_ranks
)

print("PRIORITY VALIDATION\n")

for name, count in checks.items():
    print(f"{name}: {count}")

if sum(checks.values()) == 0:
    print("\nPASS: All listed priority checks passed.")
else:
    print("\nREVIEW: Fix flagged results before continuing.")

print("\nDraft scoring observations:")
print("Customers in call list:", len(call_list))
print(
    "Customers scoring 100:",
    int(call_list["priority_score"].eq(100).sum())
)

if not call_list.empty:
    high_percentage = (
        call_list["priority_category"].eq("High").mean() * 100
    )
    print(
        "High-priority share of call list:",
        f"{high_percentage:.1f}%"
    )