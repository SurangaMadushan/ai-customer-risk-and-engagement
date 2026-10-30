# Draft Threshold Review

Model: Random Forest
Target: Invoice unpaid 30 days after its due date.
Data: Synthetic.
Thresholds compared using validation data only.

At threshold 0.5:
- Precision: 50.9%
- Recall: 99.9%
- False alarms: 1,495
- Missed unpaid invoices: 1

At threshold 0.8:
- Precision: 88.7%
- Recall: 58.9%
- False alarms: 116
- Missed unpaid invoices: 637

Threshold 0.8 achieved the highest validation F1 among
the thresholds tested.

Decision pending:
The team must agree on the acceptable balance between
unnecessary collection contacts and missed unpaid invoices.

The saved model metadata still uses threshold 0.5.
The original final test results were measured at threshold 0.5.