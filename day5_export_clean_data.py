import pandas as pd

print("==========================================")
print("DAY 5: CLEANING & EXPORTING FINAL DATASETS")
print("==========================================")

# Load raw datasets from Day 3
df_customers = pd.read_csv('raw_customers.csv')
df_invoices = pd.read_csv('raw_invoices.csv')
df_payments = pd.read_csv('raw_payments.csv')

print("\n1. Cleaning Datasets...")

# Remove temporary behavior column from customers before export
if 'behavior_category' in df_customers.columns:
    df_customers_clean = df_customers.drop(columns=['behavior_category'])
else:
    df_customers_clean = df_customers.copy()

print("\n2. Exporting Final Clean CSV Files...")

# Export final files as requested
df_customers_clean.to_csv('customers.csv', index=False)
df_invoices.to_csv('invoices.csv', index=False)
df_payments.to_csv('payments.csv', index=False)

print("\n------------------------------------------")
print("FINAL DELIVERABLES GENERATED SUCCESSFULLY:")
print("  1. customers.csv  (Shape: ", df_customers_clean.shape, ")")
print("  2. invoices.csv   (Shape: ", df_invoices.shape, ")")
print("  3. payments.csv   (Shape: ", df_payments.shape, ")")
print("------------------------------------------")

print("\n==========================================")
print("ALL 5 DAYS TASKS COMPLETED SUCCESSFULLY! 🎉")
print("==========================================")