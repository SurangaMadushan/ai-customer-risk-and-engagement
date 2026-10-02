# Day 1 - Identifying Main Datasets and Data Structure

print("==========================================")
print("DAY 1: IDENTIFYING REQUIRED DATASETS")
print("==========================================")

# Datasets definition according to SLT Requirements
datasets = {
    "1. Customer Data": ["customer_id", "customer_type", "service_type", "package"],
    "2. Invoice Data": ["invoice_id", "customer_id", "invoice_date", "due_date", "invoice_amount", "outstanding_amount", "status"],
    "3. Payment Data": ["payment_id", "invoice_id", "customer_id", "payment_date", "payment_amount"]
}

for name, fields in datasets.items():
    print(f"\n{name}:")
    for field in fields:
        print(f"  - {field}")

print("\n==========================================")
print("Day 1 Requirements Identified Successfully!")
print("==========================================")