from pathlib import Path
import pandas as pd

project_folder = Path(__file__).parent
data_folder = project_folder / "data"
output_folder = project_folder / "outputs"
output_folder.mkdir(exist_ok=True)

ASSESSMENT_DATE = pd.Timestamp("2026-02-10")

customers = pd.read_csv(data_folder / "customers.csv")
invoices = pd.read_csv(data_folder / "invoices.csv")
payments = pd.read_csv(data_folder / "payments.csv")

# Convert dates.
invoices["invoice_date"] = pd.to_datetime(invoices["invoice_date"])
invoices["due_date"] = pd.to_datetime(invoices["due_date"])
payments["payment_date"] = pd.to_datetime(payments["payment_date"])

# Only use information available by the assessment date.
invoices = invoices[
    invoices["invoice_date"] <= ASSESSMENT_DATE
].copy()

payments = payments[
    payments["payment_date"] <= ASSESSMENT_DATE
].copy()

# Add up payments for each invoice.
paid_per_invoice = (
    payments.groupby("invoice_id", as_index=False)
    .agg(amount_paid=("payment_amount", "sum"))
)

invoices = invoices.merge(
    paid_per_invoice,
    on="invoice_id",
    how="left",
    validate="one_to_one",
)

invoices["amount_paid"] = invoices["amount_paid"].fillna(0)

# Recalculate balances instead of relying on the supplied status.
invoices["balance"] = (
    invoices["invoice_amount"] - invoices["amount_paid"]
).round(2)

if (invoices["balance"] < -0.01).any():
    raise ValueError("Some invoices are overpaid. Review before continuing.")

invoices["balance"] = invoices["balance"].clip(lower=0)

invoices["is_overdue"] = (
    (invoices["due_date"] < ASSESSMENT_DATE)
    & (invoices["balance"] > 0.01)
)

invoices["overdue_amount"] = invoices["balance"].where(
    invoices["is_overdue"], 0
)

invoices["overdue_days"] = (
    (ASSESSMENT_DATE - invoices["due_date"]).dt.days
).where(invoices["is_overdue"], 0)

# Create one summary row per customer.
summary = (
    invoices.groupby("customer_id", as_index=False)
    .agg(
        history_invoice_count=("invoice_id", "count"),
        total_outstanding=("balance", "sum"),
        total_overdue_amount=("overdue_amount", "sum"),
        overdue_invoice_count=("is_overdue", "sum"),
        max_overdue_days=("overdue_days", "max"),
    )
)

features = customers.merge(
    summary,
    on="customer_id",
    how="left",
    validate="one_to_one",
)

numeric_columns = [
    "history_invoice_count",
    "total_outstanding",
    "total_overdue_amount",
    "overdue_invoice_count",
    "max_overdue_days",
]

features[numeric_columns] = features[numeric_columns].fillna(0)

for column in [
    "history_invoice_count",
    "overdue_invoice_count",
    "max_overdue_days",
]:
    features[column] = features[column].astype(int)

for column in ["total_outstanding", "total_overdue_amount"]:
    features[column] = features[column].round(2)

features["assessment_date"] = ASSESSMENT_DATE.strftime("%Y-%m-%d")

output_file = output_folder / "customer_features.csv"
features.to_csv(output_file, index=False)

print("Assessment date:", ASSESSMENT_DATE.date())
print("Customers:", len(features))
print(
    "Customers with overdue balances:",
    (features["total_overdue_amount"] > 0.01).sum(),
)
print("\nFirst five customers:")
print(features.head().to_string(index=False))
print("\nSaved to:", output_file)