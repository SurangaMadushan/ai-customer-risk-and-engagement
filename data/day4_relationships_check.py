import pandas as pd

print("==========================================")
print("DAY 4: DATA STRUCTURE & RELATIONSHIPS CHECK")
print("==========================================")

# Load generated raw datasets
df_customers = pd.read_csv('raw_customers.csv')
df_invoices = pd.read_csv('raw_invoices.csv')
df_payments = pd.read_csv('raw_payments.csv')

print("\n1. Checking Data Shapes:")
print(f"   - Customers Shape : {df_customers.shape}")
print(f"   - Invoices Shape  : {df_invoices.shape}")
print(f"   - Payments Shape  : {df_payments.shape}")

print("\n2. Verifying Foreign Key Integrity (Relationships):")

# Relationship 1: Customer -> Invoice
orphan_invoices = df_invoices[~df_invoices['customer_id'].isin(df_customers['customer_id'])]
print(f"   - Invoices with missing Customer IDs: {len(orphan_invoices)}")

# Relationship 2: Customer -> Payment
orphan_payments_cust = df_payments[~df_payments['customer_id'].isin(df_customers['customer_id'])]
print(f"   - Payments with missing Customer IDs: {len(orphan_payments_cust)}")

# Relationship 3: Invoice -> Payment
orphan_payments_inv = df_payments[~df_payments['invoice_id'].isin(df_invoices['invoice_id'])]
print(f"   - Payments with missing Invoice IDs : {len(orphan_payments_inv)}")

print("\n3. Integrity Check Result:")
if len(orphan_invoices) == 0 and len(orphan_payments_cust) == 0 and len(orphan_payments_inv) == 0:
    print("   SUCCESS: All Relationships (Customer -> Invoice -> Payment) are 100% Valid!")
else:
    print("   ERROR: Relationship mismatch found!")

print("\n==========================================")
print("DAY 4 COMPLETED SUCCESSFULLY!")
print("==========================================")