from pathlib import Path
import pandas as pd

project_folder = Path(__file__).resolve().parent
data_folder = project_folder / "data"
output_folder = project_folder / "outputs"
output_folder.mkdir(exist_ok=True)

# Draft: confirm that payment records are complete through this date.
OBSERVATION_END = pd.Timestamp("2026-02-09")

customers = pd.read_csv(data_folder / "customers.csv")
invoices = pd.read_csv(data_folder / "invoices.csv")
payments = pd.read_csv(data_folder / "payments.csv")

for column in ["invoice_date", "due_date"]:
    invoices[column] = pd.to_datetime(
        invoices[column], errors="raise"
    )

payments["payment_date"] = pd.to_datetime(
    payments["payment_date"], errors="raise"
)

payments = payments[
    payments["payment_date"] <= OBSERVATION_END
].copy()

# LABEL: amount paid by 30 days after each invoice's due date.
invoices["label_date"] = (
    invoices["due_date"] + pd.Timedelta(days=30)
)

payment_labels = payments.merge(
    invoices[["invoice_id", "label_date"]],
    on="invoice_id",
    validate="many_to_one"
)

paid_by_deadline = (
    payment_labels[
        payment_labels["payment_date"]
        <= payment_labels["label_date"]
    ]
    .groupby("invoice_id")["payment_amount"]
    .sum()
)

invoices["paid_by_deadline"] = (
    invoices["invoice_id"].map(paid_by_deadline).fillna(0)
)

invoices["unpaid_after_30_days"] = (
    invoices["invoice_amount"]
    - invoices["paid_by_deadline"] > 0.01
).astype(int)

# Only use invoices with a complete follow-up window.
eligible = invoices[
    invoices["label_date"] <= OBSERVATION_END
].copy()

training_parts = []

# Rebuild history at each invoice date.
for snapshot_date, current in eligible.groupby("invoice_date"):

    # Earlier invoices only.
    history = invoices[
        invoices["invoice_date"] < snapshot_date
    ].copy()

    # Earlier payments only: future payments cannot enter features.
    known_payments = payments[
        payments["payment_date"] < snapshot_date
    ].copy()

    known_payments = known_payments.sort_values(
        ["invoice_id", "payment_date", "payment_id"]
    )

    known_payments["cumulative_paid"] = (
        known_payments.groupby("invoice_id")[
            "payment_amount"
        ].cumsum()
    )

    paid_totals = known_payments.groupby(
        "invoice_id"
    )["payment_amount"].sum()

    history["amount_paid"] = (
        history["invoice_id"].map(paid_totals).fillna(0)
    )

    history["balance"] = (
        history["invoice_amount"] - history["amount_paid"]
    ).clip(lower=0)

    history["is_overdue"] = (
        (history["due_date"] < snapshot_date)
        & (history["balance"] > 0.01)
    )

    history["overdue_amount"] = history["balance"].where(
        history["is_overdue"], 0
    )

    history["overdue_days"] = (
        snapshot_date - history["due_date"]
    ).dt.days.where(history["is_overdue"], 0)

    summary = history.groupby("customer_id").agg(
        history_invoice_count=("invoice_id", "count"),
        total_outstanding=("balance", "sum"),
        total_overdue_amount=("overdue_amount", "sum"),
        overdue_invoice_count=("is_overdue", "sum"),
        max_overdue_days=("overdue_days", "max")
    )

    # Find fully paid historical invoices.
    payment_details = known_payments.merge(
        history[["invoice_id", "invoice_amount"]],
        on="invoice_id",
        how="inner",
        validate="many_to_one"
    )

    settled = payment_details[
        payment_details["cumulative_paid"]
        >= payment_details["invoice_amount"] - 0.01
    ]

    settlement_dates = settled.groupby("invoice_id").agg(
        full_payment_date=("payment_date", "min")
    )

    paid_history = history.merge(
        settlement_dates,
        on="invoice_id",
        how="inner",
        validate="one_to_one"
    )

    paid_history["delay"] = (
        paid_history["full_payment_date"]
        - paid_history["due_date"]
    ).dt.days.clip(lower=0)

    paid_history["paid_late"] = (
        paid_history["full_payment_date"]
        > paid_history["due_date"]
    )

    behaviour = paid_history.groupby("customer_id").agg(
        paid_invoice_count=("invoice_id", "count"),
        average_payment_delay=("delay", "mean"),
        late_payment_ratio=("paid_late", "mean")
    )

    # Explicitly select columns: exclude future balances and status.
    rows = current[
        [
            "invoice_id",
            "customer_id",
            "invoice_date",
            "due_date",
            "invoice_amount",
            "label_date",
            "unpaid_after_30_days"
        ]
    ].copy()

    rows = rows.merge(
        summary, on="customer_id", how="left",
        validate="many_to_one"
    )

    rows = rows.merge(
        behaviour, on="customer_id", how="left",
        validate="many_to_one"
    )

    zero_columns = [
        "history_invoice_count",
        "total_outstanding",
        "total_overdue_amount",
        "overdue_invoice_count",
        "max_overdue_days",
        "paid_invoice_count"
    ]

    rows[zero_columns] = rows[zero_columns].fillna(0)

    rows["has_paid_history"] = (
        rows["paid_invoice_count"] > 0
    ).astype(int)

    rows["payment_terms_days"] = (
        rows["due_date"] - rows["invoice_date"]
    ).dt.days

    # Unknown behaviour stays blank for later model preprocessing.
    training_parts.append(rows)

if not training_parts:
    raise ValueError("No invoices have complete follow-up data.")

training_data = pd.concat(training_parts, ignore_index=True)

training_data = training_data.merge(
    customers,
    on="customer_id",
    how="left",
    validate="many_to_one"
)

training_data = training_data.sort_values(
    ["invoice_date", "customer_id", "invoice_id"]
)

output_path = output_folder / "training_data.csv"
training_data.to_csv(output_path, index=False)

print("TRAINING DATA PREPARED")
print("Rows:", len(training_data))
print("Observation end:", OBSERVATION_END.date())

print("\nTarget counts:")
print(
    training_data["unpaid_after_30_days"]
    .value_counts()
    .rename(index={
        0: "Fully paid by deadline",
        1: "Unpaid balance at deadline"
    })
    .to_string()
)

print("\nInvoice dates:")
print(training_data["invoice_date"].min().date())
print(training_data["invoice_date"].max().date())

print("\nSaved to:", output_path)