from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

project_folder = Path(__file__).resolve().parent
output_folder = project_folder / "outputs"
chart_folder = output_folder / "charts"
chart_folder.mkdir(parents=True, exist_ok=True)

data = pd.read_csv(
    output_folder / "customer_features_with_behaviour.csv"
)

# Chart 1: Average payment delay.
plt.figure(figsize=(8, 5))
plt.hist(
    data["average_payment_delay"].dropna(),
    bins=30,
    edgecolor="white"
)
plt.title("Average Payment Delay per Customer")
plt.xlabel("Average delay (days; early/on-time = 0)")
plt.ylabel("Number of customers")
plt.tight_layout()
plt.savefig(chart_folder / "01_payment_delay.png", dpi=150)
plt.close()

# Chart 2: Late payment ratio.
plt.figure(figsize=(8, 5))
plt.hist(
    data["late_payment_ratio"].dropna(),
    bins=20,
    range=(0, 1),
    edgecolor="white"
)
plt.title("Late Payment Ratio per Customer")
plt.xlabel("Proportion of fully paid invoices paid late")
plt.ylabel("Number of customers")
plt.tight_layout()
plt.savefig(chart_folder / "02_late_payment_ratio.png", dpi=150)
plt.close()

# Chart 3: Overdue amounts among customers with overdue debt.
overdue = data[data["total_overdue_amount"] > 0].copy()

plt.figure(figsize=(8, 5))
plt.hist(
    overdue["total_overdue_amount"],
    bins=30,
    edgecolor="white"
)
plt.title("Overdue Amounts — Customers with Overdue Balances")
plt.xlabel("Total overdue amount (LKR)")
plt.ylabel("Number of customers")
plt.tight_layout()
plt.savefig(chart_folder / "03_overdue_amount.png", dpi=150)
plt.close()

# Chart 4: Current overdue status.
counts = pd.Series({
    "No overdue balance": (data["total_overdue_amount"] == 0).sum(),
    "Has overdue balance": (data["total_overdue_amount"] > 0).sum()
})

plt.figure(figsize=(8, 5))
bars = plt.bar(counts.index, counts.values)

for bar in bars:
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        bar.get_height(),
        str(int(bar.get_height())),
        ha="center",
        va="bottom"
    )

plt.title("Customer Overdue Status")
plt.ylabel("Number of customers")
plt.ylim(0, counts.max() * 1.15)
plt.tight_layout()
plt.savefig(chart_folder / "04_overdue_status.png", dpi=150)
plt.close()

print("Charts saved to:", chart_folder)
print("\nSummary:")
print("Total customers:", len(data))
print("Customers with overdue balances:", len(overdue))
print(
    "Customers without paid history:",
    (data["has_paid_history"] == 0).sum()
)
print(
    "Median average payment delay:",
    round(data["average_payment_delay"].median(), 2),
    "days"
)

if not overdue.empty:
    print(
        "Median overdue amount among overdue customers:",
        round(overdue["total_overdue_amount"].median(), 2),
        "LKR"
    )