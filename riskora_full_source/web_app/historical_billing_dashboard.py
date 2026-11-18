"""Step 42 — read-only, descriptive historical billing aggregates for Flask UI."""
from __future__ import annotations

import csv
import re
from pathlib import Path

YEAR_RE = re.compile(r"20[0-9]{2}")
MONTH_RE = re.compile(r"(20[0-9]{2})-([01][0-9])(?:-[0-3][0-9])?")


def _count(value):
    try:
        result = int(str(value).strip())
        return result if result >= 0 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def _rows(path: Path):
    if not path.is_file():
        return []
    try:
        # Bounded: these are expected to be annual/monthly aggregate CSVs,
        # not raw records. Do not read files accidentally replaced with large data.
        if path.stat().st_size > 1_000_000:
            return []
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error):
        return []


def historical_context(root: Path, requested_year: str = ''):
    private = root / 'outputs' / 'private_audit'
    yearly_src = _rows(private / 'step29_yearly_account_month_summary_full.csv')
    monthly_src = _rows(private / 'step29_monthly_account_activity_full.csv')

    years = []
    for src in yearly_src:
        year = str(src.get('year', '')).strip()
        if not YEAR_RE.fullmatch(year) or any(r['year'] == year for r in years):
            continue
        billing = _count(src.get('represented_billing_rows'))
        account_months = _count(src.get('account_month_rows'))
        debt_months = _count(src.get('account_months_with_positive_debt_row'))
        if debt_months > account_months:
            continue
        years.append({
            'year': year,
            'billing_rows': billing,
            'account_months': account_months,
            'distinct_accounts': _count(src.get('distinct_accounts')),
            'positive_account_months': debt_months,
            'positive_share': round(100 * debt_months / account_months, 2) if account_months else 0.0,
        })
    years.sort(key=lambda r: r['year'])

    valid_years = {r['year'] for r in years}
    months = []
    seen_months = set()
    for src in monthly_src:
        raw_date = str(src.get('bill_month', '')).strip()
        match = MONTH_RE.fullmatch(raw_date)
        if not match:
            continue
        year, month_num = match.group(1), int(match.group(2))
        if year not in valid_years or not 1 <= month_num <= 12:
            continue
        month = year + '-' + str(month_num).zfill(2)
        if month in seen_months:
            continue
        count = _count(src.get('account_month_rows'))
        positive = _count(src.get('account_months_with_positive_debt_row'))
        if positive > count:
            continue
        seen_months.add(month)
        months.append({
            'year': year, 'month': month,
            'account_months': count,
            'billing_rows': _count(src.get('billing_records')),
            'positive_account_months': positive,
            'positive_share': round(100 * positive / count, 2) if count else 0.0,
        })
    months.sort(key=lambda r: r['month'])

    selected_year = requested_year if requested_year in valid_years else (years[-1]['year'] if years else '')
    shown = [m for m in months if m['year'] == selected_year]
    max_months = max((r['account_months'] for r in shown), default=0)
    for row in shown:
        row['bar_width'] = round(row['account_months'] / max_months * 100, 1) if max_months else 0.0

    return {
        'historical_available': bool(years),
        'monthly_available': bool(months),
        'years': years,
        'selected_year': selected_year,
        'months': shown,
        'total_billing_rows': sum(r['billing_rows'] for r in years),
        'total_account_months': sum(r['account_months'] for r in years),
        'total_positive_account_months': sum(r['positive_account_months'] for r in years),
        'total_years': len(years),
    }
