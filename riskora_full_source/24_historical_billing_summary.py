"""Step 24 — read-only historical billing trends, 2021–2026.

Designed for the SLT 8-column masked Parquet billing datasets.
Produces only aggregate monthly/yearly summaries; NO account identifiers,
customer records, risk scores, or model predictions are exported.

From SLT_Payment_Project root:
    python 24_historical_billing_summary.py --sample-batches 2
    python 24_historical_billing_summary.py

Input:  data/historical/DIM_BILL_YYYY_masked.parquet
Output: outputs/private_audit/step24_billing_monthly_summary.csv
        outputs/private_audit/step24_billing_yearly_summary.csv
        outputs/private_audit/step24_billing_summary_notes.json

Requires: pip install pandas pyarrow
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'data' / 'historical'
OUT = ROOT / 'outputs' / 'private_audit'
AMOUNTS = ('invoice_net_amt', 'payments_amt', 'outstanding_debt_amt', 'balance_fwd_amt')
COLS = ('bill_dtm', 'payment_due_dtm', *AMOUNTS)


def blank():
    return {
        'billing_rows': 0,
        'positive_outstanding_rows': 0,
        'zero_outstanding_rows': 0,
        'negative_outstanding_rows': 0,
        'due_before_bill_rows': 0,
        'due_over_365_days_rows': 0,
        'invalid_bill_date_rows': 0,
        **{f'{col}_row_sum': 0.0 for col in AMOUNTS},
        **{f'{col}_negative_rows': 0 for col in AMOUNTS},
        **{f'{col}_missing_rows': 0 for col in AMOUNTS},
    }


def ingest_batch(batch, file_year: int, totals: dict, pd):
    df = batch.to_pandas()
    if df.empty:
        return
    bill = pd.to_datetime(df['bill_dtm'], errors='coerce', utc=True)
    due = pd.to_datetime(df['payment_due_dtm'], errors='coerce', utc=True)
    months = bill.dt.strftime('%Y-%m').fillna('INVALID')
    diff = (due - bill).dt.total_seconds() / 86400.0

    # Calculate each month's aggregates without storing raw account/customer IDs.
    for month, idx in months.groupby(months, sort=False).groups.items():
        group = df.loc[idx]
        stat = totals[(file_year, str(month))]
        stat['billing_rows'] += len(group)
        out = pd.to_numeric(group['outstanding_debt_amt'], errors='coerce')
        stat['positive_outstanding_rows'] += int(out.gt(0).sum())
        stat['zero_outstanding_rows'] += int(out.eq(0).sum())
        stat['negative_outstanding_rows'] += int(out.lt(0).sum())
        stat['due_before_bill_rows'] += int(diff.loc[idx].lt(0).sum())
        stat['due_over_365_days_rows'] += int(diff.loc[idx].gt(365).sum())
        stat['invalid_bill_date_rows'] += int(bill.loc[idx].isna().sum())
        for col in AMOUNTS:
            v = pd.to_numeric(group[col], errors='coerce')
            stat[f'{col}_row_sum'] += float(v.sum())
            stat[f'{col}_negative_rows'] += int(v.lt(0).sum())
            stat[f'{col}_missing_rows'] += int(v.isna().sum())


def serialize(row):
    return {k: round(v, 2) if k.endswith('_row_sum') else v for k, v in row.items()}


def write_csv(dest, rows):
    if not rows:
        raise ValueError('No rows to export')
    with dest.open('w', newline='', encoding='utf-8') as fp:
        wr = csv.DictWriter(fp, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description='Aggregate historical Parquet billing trends without exposing account records')
    ap.add_argument('--sample-batches', type=int, default=0, metavar='N', help='Read first N batches per year; omit for all records')
    ap.add_argument('--batch-size', type=int, default=65536)
    args = ap.parse_args()
    if args.sample_batches < 0 or args.batch_size < 1000:
        ap.error('sample-batches must be >= 0 and batch-size >= 1000')
    try:
        import pandas as pd
        import pyarrow.parquet as pq
    except ImportError as e:
        print('Missing dependency:', e)
        print('Install: python -m pip install pandas pyarrow')
        return 1

    files = sorted(SOURCE.glob('DIM_BILL_*_masked.parquet'))
    print('\nSLT STEP 24 — HISTORICAL BILLING MONTHLY/YEARLY SUMMARY')
    print('Mode:', 'SAMPLE' if args.sample_batches else 'FULL')
    if not files:
        print('No Parquet files at', SOURCE)
        return 1
    totals = defaultdict(blank)
    processed = 0
    valid_files = 0
    years = []
    errors = []
    for file in files:
        match = re.fullmatch(r'DIM_BILL_(\d{4})_masked\.parquet', file.name)
        if not match:
            continue
        year = int(match.group(1))
        years.append(year)
        try:
            pf = pq.ParquetFile(str(file))
            missing = sorted(set(COLS) - set(pf.schema.names))
            if missing:
                raise ValueError('Missing fields: ' + ', '.join(missing))
            nrows = 0
            for i, batch in enumerate(pf.iter_batches(batch_size=args.batch_size, columns=list(COLS)), start=1):
                if args.sample_batches and i > args.sample_batches:
                    break
                ingest_batch(batch, year, totals, pd)
                nrows += batch.num_rows
            processed += nrows
            valid_files += 1
            print(f'  {file.name}: {nrows:,} of {pf.metadata.num_rows:,} records processed')
        except Exception as exc:
            errors.append(f'{file.name}: {type(exc).__name__}: {exc}')
            print('  ERROR:', errors[-1])
    if errors:
        print('Errors detected; reports NOT written. Review these errors first.')
        return 1

    monthly = [dict(year=yr, billing_month=month, **serialize(stat))
               for (yr, month), stat in sorted(totals.items())]
    if not monthly:
        print('No records processed.')
        return 1
    yearly_totals = defaultdict(blank)
    for (yr, month), row in totals.items():
        to = yearly_totals[yr]
        for field, value in row.items():
            to[field] += value
    yearly = [dict(year=yr, **serialize(row)) for yr, row in sorted(yearly_totals.items())]
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / 'step24_billing_monthly_summary.csv', monthly)
    write_csv(OUT / 'step24_billing_yearly_summary.csv', yearly)
    gap = sorted(set(range(min(years), max(years) + 1)) - set(years)) if years else []
    notes = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'mode': 'sample' if args.sample_batches else 'full',
        'processed_rows': processed,
        'files_processed': valid_files,
        'years_in_files': sorted(set(years)),
        'missing_years': gap,
        'monthly_rows': len(monthly),
        'important_cautions': [
            'This is a descriptive aggregation, NOT a credit score, probability of default, or predictive model.',
            'Summing outstanding_debt_amt across billing records may double-count carried-forward debt. These are row sums, NOT a verified accounts-receivable balance.',
            'Payments_amt has no transaction date in this dataset; it does NOT establish on-time payment or individual settlement behavior.',
            'Billing rows may include multiple products or duplicate invoices; no invoice unique key is available. Do not treat row count as bill count.',
            'Masked account IDs have not been verified stable across years. No account-level joins are performed.',
            '2025 file is absent; 2026 may be incomplete.',
            'Long due dates, negative amounts and missing identifiers need business-owner validation.',
            'No raw account/customer rows or identifiers are written to the reports.',
        ],
    }
    (OUT / 'step24_billing_summary_notes.json').write_text(json.dumps(notes, indent=2), encoding='utf-8')
    print('\nOutput:')
    for fn in ('step24_billing_monthly_summary.csv', 'step24_billing_yearly_summary.csv', 'step24_billing_summary_notes.json'):
        print(' -', OUT / fn)
    print(f'RESULT: {processed:,} records summarized across {len(monthly)} year-month groups; {len(gap)} missing filename year(s): {gap}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
