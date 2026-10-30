from pathlib import Path
import pandas as pd

project_folder = Path(__file__).resolve().parent
data_folder = project_folder / "data"
output_folder = project_folder / "outputs"

# Same draft assessment date used in script 03.
assessment_date = pd.Timestamp("2026-02-10")

customers = pd.read_csv(output_folder / "customer_features.csv")
invoices = pd.read_csv(data_folder / "invoices.csv")
payments = pd.read_csv(data_folder / "payments.csv")

if not customers["assessment_date"].eq(
    assessment_date.strftime("%Y-%m-%d")
).all():
    raise ValueError("Assessment dates do not match script 03.")

# Convert date columns.
for column in ["invoice_date", "due_date"]:
    invoices[column] = pd.to_datetime(
        invoices[column], errors="raise"
    )

payments["payment_date"] = pd.to_datetime(
    payments["payment_date"], errors="raise"
)

# Use information available by the assessment date.
invoices = invoices[
    invoices["invoice_date"] <= assessment_date
].copy()

payments = payments[
    payments["payment_date"] <= assessment_date
].copy()

# Track cumulative payments so partial payments are supported.
payments = payments.sort_values(
    ["invoice_id", "payment_date", "payment_id"]
)

payments["cumulative_paid"] = payments.groupby(
    "invoice_id"
)["payment_amount"].cumsum()

payment_details = payments.merge(
    invoices[["invoice_id", "invoice_amount"]],
    on="invoice_id",
    how="inner",
    validate="many_to_one"
)

# Find the first date each invoice became fully paid.
settled = payment_details[
    payment_details["cumulative_paid"]
    >= payment_details["invoice_amount"] - 0.01
]

settlement_dates = (
    settled.groupby("invoice_id", as_index=False)
    .agg(full_payment_date=("payment_date", "min"))
)

paid_invoices = invoices.merge(
    settlement_dates,
    on="invoice_id",
    how="inner",
    validate="one_to_one"
)

paid_invoices["payment_delay_days"] = (
    paid_invoices["full_payment_date"]
    - paid_invoices["due_date"]
).dt.days.clip(lower=0)

paid_invoices["paid_late"] = (
    paid_invoices["full_payment_date"]
    > paid_invoices["due_date"]
)

# Summarise payment behaviour for each customer.
behaviour = (
    paid_invoices.groupby("customer_id", as_index=False)
    .agg(
        paid_invoice_count=("invoice_id", "count"),
        late_paid_invoice_count=("paid_late", "sum"),
        average_payment_delay=("payment_delay_days", "mean"),
        late_payment_ratio=("paid_late", "mean")
    )
)

result = customers.merge(
    behaviour,
    on="customer_id",
    how="left",
    validate="one_to_one"
)

# No paid invoices means no observed payment history.
for column in ["paid_invoice_count", "late_paid_invoice_count"]:
    result[column] = result[column].fillna(0).astype(int)

result["has_paid_history"] = (
    result["paid_invoice_count"] > 0
).astype(int)

# Leave delay and ratio blank when no paid history exists.
result["average_payment_delay"] = (
    result["average_payment_delay"].round(2)
)
result["late_payment_ratio"] = (
    result["late_payment_ratio"].round(4)
)

output_path = output_folder / "customer_features_with_behaviour.csv"
result.to_csv(output_path, index=False)

print("Customers:", len(result))
print(
    "Customers without paid history:",
    (result["has_paid_history"] == 0).sum()
)

display_columns = [
    "customer_id",
    "paid_invoice_count",
    "late_paid_invoice_count",
    "average_payment_delay",
    "late_payment_ratio",
    "has_paid_history"
]

print("\nFirst five customers:")
print(result[display_columns].head().to_string(index=False))

print("\nSaved to:", output_path)