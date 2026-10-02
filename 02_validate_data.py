from pathlib import Path
import pandas as pd

data_folder = Path(__file__).parent / "data"

customers = pd.read_csv(data_folder / "customers.csv")
invoices = pd.read_csv(data_folder / "invoices.csv")
payments = pd.read_csv(data_folder / "payments.csv")

# Convert date text into actual dates.
for column in ["invoice_date", "due_date"]:
    invoices[column] = pd.to_datetime(
        invoices[column], errors="coerce"
    )

payments["payment_date"] = pd.to_datetime(
    payments["payment_date"], errors="coerce"
)

print("DATE RANGES")
for table, column in [
    (invoices, "invoice_date"),
    (invoices, "due_date"),
    (payments, "payment_date"),
]:
    print(
        f"{column}: "
        f"{table[column].min()} to {table[column].max()}"
    )

checks = {
    "Duplicate customer IDs": customers["customer_id"].duplicated().sum(),
    "Duplicate invoice IDs": invoices["invoice_id"].duplicated().sum(),
    "Duplicate payment IDs": payments["payment_id"].duplicated().sum(),
    "Invalid invoice dates": invoices["invoice_date"].isna().sum(),
    "Invalid due dates": invoices["due_date"].isna().sum(),
    "Invalid payment dates": payments["payment_date"].isna().sum(),
    "Invoices with unknown customers": (
        ~invoices["customer_id"].isin(customers["customer_id"])
    ).sum(),
    "Payments with unknown invoices": (
        ~payments["invoice_id"].isin(invoices["invoice_id"])
    ).sum(),
    "Due dates before invoice dates": (
        invoices["due_date"] < invoices["invoice_date"]
    ).sum(),
    "Negative invoice amounts": (invoices["invoice_amount"] < 0).sum(),
    "Negative payment amounts": (payments["payment_amount"] < 0).sum(),
}

linked = payments.merge(
    invoices[["invoice_id", "customer_id", "invoice_date"]],
    on="invoice_id",
    how="left",
    suffixes=("_payment", "_invoice"),
    validate="many_to_one",
)

checks["Payments before invoice dates"] = (
    linked["payment_date"] < linked["invoice_date"]
).sum()

checks["Payment/invoice customer mismatch"] = (
    linked["customer_id_payment"] != linked["customer_id_invoice"]
).sum()

print("\nVALIDATION RESULTS")
for name, count in checks.items():
    print(f"{name}: {count}")

if all(count == 0 for count in checks.values()):
    print("\nAll listed checks passed.")
else:
    print("\nSome checks need investigation.")