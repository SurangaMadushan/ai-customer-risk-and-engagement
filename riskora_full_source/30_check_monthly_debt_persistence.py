"""SLT Step 30 — descriptive within-year outstanding-indicator transitions.

Run from the existing SLT_Payment_Project root:
    python 30_check_monthly_debt_persistence.py --sample
    python 30_check_monthly_debt_persistence.py

Requires the DuckDB created by Step 29 (sample or full). Reads the existing
private account-month database, writes ONLY aggregate reports, and does not
change the Flask website, raw datasets, accounts, or scores.

IMPORTANT: positive outstanding-debt *row indicator* is NOT confirmed customer
non-payment, default, or creditworthiness. This script does not train a model.
Do not publish data or private audit outputs to GitHub.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'outputs' / 'private_audit'
YEARS = (2021, 2022, 2023, 2024, 2026)


def save_csv(path: Path, fieldnames: list[str], records: list[dict]) -> None:
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser(description='Analyze next-month outstanding indicators, NOT non-payment')
    parser.add_argument('--sample', action='store_true', help='Read the Step 29 sample database')
    parser.add_argument('--memory-limit', default='1GB', help='DuckDB memory limit, e.g. 1GB or 2GB')
    args = parser.parse_args()
    import re
    if not re.fullmatch(r'\d+(?:GB|MB)', args.memory_limit.upper()):
        parser.error('--memory-limit must resemble 1GB, 2GB or 2048MB')
    try:
        import duckdb
    except ImportError:
        print('ERROR: DuckDB is missing. Run: python -m pip install duckdb')
        return 1

    mode = 'sample' if args.sample else 'full'
    db = OUT / f'step29_private_account_month_{mode}.duckdb'
    if not db.is_file():
        print(f'ERROR: Missing Step 29 database: {db}')
        print(f'Run: python 29_prepare_account_month_features.py {"--sample-rows 20000" if args.sample else ""}')
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    temp_dir = OUT / f'step30_temp_{mode}'
    temp_dir.mkdir(exist_ok=True)

    print('\nSLT STEP 30 — WITHIN-YEAR MONTHLY OUTSTANDING INDICATOR TRANSITIONS')
    print('Mode:', mode.upper(), '| Step 29 private database:', db.name)
    print('No account IDs are exported; no scores or non-payment targets are generated.')
    print('Compare only consecutive months within each year; 2025 is not required.')

    con = None
    counts = []
    rate_rows = []
    pair_totals = {}
    try:
        con = duckdb.connect(str(db), read_only=True)
        con.execute(f"SET memory_limit='{args.memory_limit.upper()}'")
        con.execute('SET threads=2')
        con.execute("SET temp_directory='" + temp_dir.as_posix().replace("'", "''") + "'")
        existing = {r[0] for r in con.execute('SHOW TABLES').fetchall()}
        for year in YEARS:
            table = f'account_month_{year}'
            if table not in existing:
                print(f'ERROR: Missing table {table} in {db.name}. Rerun Step 29.')
                return 1
            print(f'Checking {year} ...', flush=True)
            # Joining only consecutive months prevents accidental treatment of gaps
            # as outcomes and avoids all cross-year account matching assumptions.
            results = con.execute(f"""
                SELECT
                    CASE WHEN a.positive_debt_row_count > 0 THEN 1 ELSE 0 END AS prior_positive,
                    CASE WHEN b.positive_debt_row_count > 0 THEN 1 ELSE 0 END AS next_positive,
                    COUNT(*)::BIGINT AS pairs
                FROM {table} AS a
                JOIN {table} AS b
                  ON a.masked_account_id = b.masked_account_id
                 AND b.bill_month = CAST(a.bill_month + INTERVAL '1 month' AS DATE)
                GROUP BY 1, 2
                ORDER BY 1, 2
            """).fetchall()
            lookup = {(int(p), int(n)): int(c) for p, n, c in results}
            total_pairs = sum(lookup.values())
            pair_totals[year] = total_pairs
            print(f'  Consecutive-month account pairs: {total_pairs:,}', flush=True)
            for prior in (0, 1):
                group_n = lookup.get((prior, 0), 0) + lookup.get((prior, 1), 0)
                with_next = lookup.get((prior, 1), 0)
                rate = round(100.0 * with_next / group_n, 3) if group_n else None
                rate_rows.append({
                    'year': year,
                    'prior_month_has_positive_outstanding_row': prior,
                    'consecutive_month_pairs': group_n,
                    'next_month_has_positive_outstanding_row_pairs': with_next,
                    'next_month_positive_share_pct': '' if rate is None else rate,
                })
                for nxt in (0, 1):
                    counts.append({
                        'year': year,
                        'prior_month_has_positive_outstanding_row': prior,
                        'next_month_has_positive_outstanding_row': nxt,
                        'pair_count': lookup.get((prior, nxt), 0),
                    })

        count_path = OUT / f'step30_monthly_indicator_transition_counts_{mode}.csv'
        rates_path = OUT / f'step30_monthly_indicator_transition_rates_{mode}.csv'
        notes_path = OUT / f'step30_monthly_indicator_notes_{mode}.md'
        save_csv(count_path, ['year', 'prior_month_has_positive_outstanding_row',
                              'next_month_has_positive_outstanding_row', 'pair_count'], counts)
        save_csv(rates_path, ['year', 'prior_month_has_positive_outstanding_row',
                             'consecutive_month_pairs', 'next_month_has_positive_outstanding_row_pairs',
                             'next_month_positive_share_pct'], rate_rows)
        lines = [
            '# Step 30 — Monthly outstanding-indicator transitions',
            '',
            f'Generated (UTC): {datetime.now(timezone.utc).isoformat()}',
            f'Mode: {mode}',
            '',
            '## Interpretation',
            'The analysis compares a masked account with a record in a billing month '
            'to the same masked account with a record in the immediately following billing month, within a year.',
            'The indicator is whether **any source billing row has outstanding_debt_amt > 0** in that account-month.',
            '**This is not verified invoice non-payment, default, delinquency, or creditworthiness.**',
            'Monthly outstanding observations may contain carried balances, timing differences, or adjustments.',
            '',
            '## Checks and limitations',
            '- Account identifiers are matched only within a year. No cross-year joins.',
            '- Pairs require a record in two consecutive calendar months; months without a record are not labeled zero.',
            '- Multiple product records within an account-month have been aggregated by Step 29.',
            '- Positive outstanding is a descriptive field indicator, not an SLT-approved label.',
            '- Data-owner definitions of payments_amt and outstanding_debt_amt remain unconfirmed.',
            '- 2026 is a partial year; 2025 is intentionally not included.',
            '- Sample mode uses first rows of each Parquet file and may contain few or zero consecutive months.',
            '- Do not derive creditworthiness weights or predictive-accuracy metrics from these transition rates.',
            '- Do not publish the Step 29 DuckDB or private output files to GitHub.',
            '',
            '## Consecutive-month pair counts',
        ]
        for yr, n in pair_totals.items():
            lines.append(f'- {yr}: {n:,}')
        lines.extend(['', '## Next requirement',
                      'Confirm what outstanding_debt_amt represents at the billing-row level and define a true future payment outcome before training a non-payment model.'])
        notes_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print('\nSaved:', count_path.relative_to(ROOT))
        print('Saved:', rates_path.relative_to(ROOT))
        print('Saved:', notes_path.relative_to(ROOT))
        print('RESULT: Descriptive transitions completed; no credit scores or non-payment labels created.')
        return 0
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}')
        print('Original Parquet data and Flask website were not changed.')
        return 1
    finally:
        if con is not None:
            con.close()


if __name__ == '__main__':
    sys.exit(main())
