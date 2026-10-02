from pathlib import Path
import pandas as pd

data_folder = Path(__file__).parent / "data"

customers = pd.read_csv(data_folder / "customers.csv")
invoices = pd.read_csv(data_folder / "invoices.csv")
payments = pd.read_csv(data_folder / "payments.csv")

for name, table in [
    ("Customers", customers),
    ("Invoices", invoices),
    ("Payments", payments),
]:
    print(f"\n{name}: {len(table):,} rows")
    print("Columns:", table.columns.tolist())
    print(table.head())
    print("\nMissing values:")
    print(table.isna().sum())