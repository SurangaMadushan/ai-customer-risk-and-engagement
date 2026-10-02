import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

print("==========================================")
print("DAY 3: GENERATING SYNTHETIC DATASETS")
print("==========================================")

# Randomness control
random.seed(42)
np.random.seed(42)

# Target counts
NUM_CUSTOMERS = 10000

print(f"Generating {NUM_CUSTOMERS} Customers...")

# 1. Customer Master Generation
categories = ['Regular Payer', 'Predictable Late Payer', 'Irregular Payer', 'High-Risk Non-Payer']
weights = [0.4, 0.3, 0.2, 0.1] # Distribution ratios

customers_list = []
for i in range(1, NUM_CUSTOMERS + 1):
    c_id = f"C{i:05d}"
    c_type = random.choice(['Residential', 'SME', 'Enterprise'])
    s_type = random.choice(['Fiber', 'LTE', 'ADSL'])
    pkg = random.choice(['Web Family', 'Web Booster', 'Biz Speed'])
    behavior = random.choices(categories, weights=weights)[0]
    
    customers_list.append({
        'customer_id': c_id,
        'customer_type': c_type,
        'service_type': s_type,
        'package': pkg,
        'behavior_category': behavior
    })

df_customers = pd.DataFrame(customers_list)

print("Generating Invoices (~120k) and Payments (~100k)...")

invoices_list = []
payments_list = []
start_date = datetime(2025, 1, 1)

# Generate 12 billing cycles (months) per customer = 10,000 * 12 = 120,000 Invoices
for idx, row in df_customers.iterrows():
    c_id = row['customer_id']
    behavior = row['behavior_category']
    
    for m in range(12): 
        inv_id = f"INV_{c_id}_{m+1:02d}"
        inv_date = start_date + timedelta(days=m*30)
        due_date = inv_date + timedelta(days=14)
        inv_amount = round(random.uniform(1500, 15000), 2)
        
        # Payment behaviors based on customer group
        if behavior == 'Regular Payer':
            delay = random.randint(-5, 0)
            is_paid = True
        elif behavior == 'Predictable Late Payer':
            delay = random.randint(3, 6) # Consistently 3-6 days late
            is_paid = True
        elif behavior == 'Irregular Payer':
            delay = random.randint(1, 25)
            is_paid = random.choice([True, True, False]) # ~66% paid
        else: # High-Risk Non-Payer
            delay = random.randint(20, 60)
            is_paid = random.choice([True, False, False]) # ~33% paid
            
        status = 'Paid' if is_paid else 'Overdue'
        out_amount = 0.0 if is_paid else inv_amount
        
        invoices_list.append({
            'invoice_id': inv_id,
            'customer_id': c_id,
            'invoice_date': inv_date.strftime('%Y-%m-%d'),
            'due_date': due_date.strftime('%Y-%m-%d'),
            'invoice_amount': inv_amount,
            'outstanding_amount': out_amount,
            'status': status
        })
        
        if is_paid:
            pay_date = due_date + timedelta(days=delay)
            payments_list.append({
                'payment_id': f"PAY_{inv_id}",
                'invoice_id': inv_id,
                'customer_id': c_id,
                'payment_date': pay_date.strftime('%Y-%m-%d'),
                'payment_amount': inv_amount
            })

df_invoices = pd.DataFrame(invoices_list)
df_payments = pd.DataFrame(payments_list)

# Temporary save to check integrity in Day 4
df_customers.to_csv('raw_customers.csv', index=False)
df_invoices.to_csv('raw_invoices.csv', index=False)
df_payments.to_csv('raw_payments.csv', index=False)

print("\n------------------------------------------")
print(f"Generated Customers : {len(df_customers):,}")
print(f"Generated Invoices  : {len(df_invoices):,}")
print(f"Generated Payments  : {len(df_payments):,}")
print("------------------------------------------")
print("DAY 3 COMPLETED SUCCESSFULLY!")
print("==========================================")