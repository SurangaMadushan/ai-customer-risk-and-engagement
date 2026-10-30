from pathlib import Path

import pandas as pd


project_folder = Path(__file__).resolve().parent
data_folder = project_folder / "data"
output_folder = project_folder / "outputs"
output_folder.mkdir(exist_ok=True)

workbook_path = data_folder / "SLT_Payment_Behaviour_Dummy_Data.xlsx"
customers = pd.read_excel(
    workbook_path,
    sheet_name="Behaviour_Summary",
)

required_columns = [
    "account_no",
    "customer_id",
    "unpaid_past_due_bills",
    "current_outstanding_LKR",
    "current_overdue_days",
]
missing_columns = sorted(set(required_columns) - set(customers.columns))
if missing_columns:
    raise ValueError(f"Workbook is missing required columns: {missing_columns}")

if customers[["account_no", "customer_id"]].isna().any().any():
    raise ValueError("Account and customer IDs must not be missing.")

if customers["account_no"].duplicated().any():
    raise ValueError("Expected one summary row per account.")

numeric_columns = [
    "unpaid_past_due_bills",
    "current_outstanding_LKR",
    "current_overdue_days",
]
for column in numeric_columns:
    customers[column] = pd.to_numeric(customers[column], errors="raise")

is_defaulting = (
    customers["unpaid_past_due_bills"].gt(0)
    & customers["current_outstanding_LKR"].gt(0)
    & customers["current_overdue_days"].gt(0)
)

defaulting = customers.loc[is_defaulting].copy()
defaulting = defaulting.sort_values(
    ["current_overdue_days", "current_outstanding_LKR", "account_no"],
    ascending=[False, False, True],
)

# Keep ground-truth labels out of the operational collection list.
output_columns = [
    "account_no",
    "customer_id",
    "customer_type",
    "unpaid_past_due_bills",
    "current_outstanding_LKR",
    "current_overdue_days",
    "suspensions",
    "collection_actions",
    "agent_classification",
    "recommended_action",
    "reason",
]
output_columns = [
    column for column in output_columns if column in defaulting.columns
]

output_path = output_folder / "defaulting_customers.csv"
defaulting[output_columns].to_csv(output_path, index=False)

print("CURRENT DEFAULTING CUSTOMERS")
print("Accounts assessed:", len(customers))
print("Accounts with overdue unpaid balances:", len(defaulting))
print(
    "Total current outstanding (LKR):",
    round(defaulting["current_outstanding_LKR"].sum(), 2),
)

if "designed_profile (ground truth)" in defaulting.columns:
    print("\nGround-truth profile breakdown (validation only):")
    print(
        defaulting["designed_profile (ground truth)"]
        .value_counts()
        .to_string()
    )

print("\nSaved:", output_path)