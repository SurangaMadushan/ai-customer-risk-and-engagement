import pandas as pd

print("==========================================")
print("DAY 2: GENERATING DATA DICTIONARY")
print("==========================================")

# Defining Data Dictionary Structure
data_dictionary = [
    # Customer Master Table
    {"Table": "customers", "Field Name": "customer_id", "Data Type": "String (Primary Key)", "Description": "Unique identifier for customer (e.g., C00001)"},
    {"Table": "customers", "Field Name": "customer_type", "Data Type": "String", "Description": "Type of customer (Residential, SME, Enterprise)"},
    {"Table": "customers", "Field Name": "service_type", "Data Type": "String", "Description": "Type of service (Fiber, LTE, ADSL)"},
    {"Table": "customers", "Field Name": "package", "Data Type": "String", "Description": "Subscribed Broadband/Telephone package"},
    {"Table": "customers", "Field Name": "behavior_category", "Data Type": "String", "Description": "Payer Category (Regular, Predictable Late, Irregular, High-Risk)"},
    
    # Invoices Table
    {"Table": "invoices", "Field Name": "invoice_id", "Data Type": "String (Primary Key)", "Description": "Unique bill/invoice ID"},
    {"Table": "invoices", "Field Name": "customer_id", "Data Type": "String (Foreign Key)", "Description": "Links invoice to customer"},
    {"Table": "invoices", "Field Name": "invoice_date", "Data Type": "Date (YYYY-MM-DD)", "Description": "Date when invoice was generated"},
    {"Table": "invoices", "Field Name": "due_date", "Data Type": "Date (YYYY-MM-DD)", "Description": "Payment due date"},
    {"Table": "invoices", "Field Name": "invoice_amount", "Data Type": "Float", "Description": "Total billed amount"},
    {"Table": "invoices", "Field Name": "outstanding_amount", "Data Type": "Float", "Description": "Unpaid balance remaining"},
    {"Table": "invoices", "Field Name": "status", "Data Type": "String", "Description": "Bill status (Paid, Overdue)"},
    
    # Payments Table
    {"Table": "payments", "Field Name": "payment_id", "Data Type": "String (Primary Key)", "Description": "Unique payment record ID"},
    {"Table": "payments", "Field Name": "invoice_id", "Data Type": "String (Foreign Key)", "Description": "Links payment to specific invoice"},
    {"Table": "payments", "Field Name": "customer_id", "Data Type": "String (Foreign Key)", "Description": "Links payment to customer"},
    {"Table": "payments", "Field Name": "payment_date", "Data Type": "Date (YYYY-MM-DD)", "Description": "Actual date payment was settled"},
    {"Table": "payments", "Field Name": "payment_amount", "Data Type": "Float", "Description": "Amount paid"}
]

# Convert to DataFrame
df_dict = pd.DataFrame(data_dictionary)

# Display in Terminal
print("\n--- Data Dictionary Preview ---")
print(df_dict.to_string(index=False))

# Export as CSV for Documentation (Member 5)
df_dict.to_csv("data_dictionary.csv", index=False)

print("\n==========================================")
print("SUCCESS: data_dictionary.csv created!")
print("==========================================")