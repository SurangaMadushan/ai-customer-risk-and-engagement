#!/usr/bin/env python3
"""SLT Step 42 — Add a historical-billing aggregate dashboard to the EXISTING Flask website.

Run from the SLT_Payment_Project root after Step 29 (full) and Step 18 staff login:

    python 42_install_historical_billing_dashboard.py
    python web_app\\app.py

Open http://127.0.0.1:5000/historical-billing (sign in as analyst/admin).

The new page uses ONLY local, aggregate Step 29 CSV reports. No private DuckDB
account-level table, masked account number, customer record, or real credit score
is exposed. The 200-customer synthetic-data demo is not replaced.

The installer backs up all modified existing files as *.step42.bak, fails closed
when the existing website layout differs, and is safe to run again.
"""
from __future__ import annotations

import ast
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web_app'
APP = WEB / 'app.py'
BASE = WEB / 'templates' / 'base.html'
AUTH = WEB / 'staff_auth.py'
VIEW = WEB / 'historical_billing_dashboard.py'
TEMPLATE = WEB / 'templates' / 'historical_billing.html'
GITIGNORE = ROOT / '.gitignore'
MARKER = '# >>> STEP 42 HISTORICAL BILLING DASHBOARD >>>'

# This module deliberately imports neither DuckDB nor Pandas. It reads a strict
# whitelist of two aggregate Step 29 CSV reports, not individual customer rows.
VIEW_SOURCE = '''"""Step 42 — read-only, descriptive historical billing aggregates for Flask UI."""
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
'''

TEMPLATE_SOURCE = '''{% extends 'base.html' %}
{% block title %}Historical billing{% endblock %}
{% block breadcrumb %}Historical billing{% endblock %}
{% block content %}
<style>
  .hb-chips {display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 22px;}
  .hb-chip {padding:8px 12px;border:1px solid #cbd6e4;border-radius:999px;text-decoration:none;color:#235075;background:#fff;font-weight:600;}
  .hb-chip.active {background:#174a79;color:white;border-color:#174a79;}
  .hb-bartrack {background:#e6edf4;height:12px;border-radius:12px;overflow:hidden;min-width:80px;}
  .hb-barfill {height:100%;background:#1684a1;border-radius:12px;}
  .hb-table {width:100%;border-collapse:collapse;}
  .hb-table th,.hb-table td {padding:11px 10px;border-bottom:1px solid #e2e8f0;text-align:left;font-size:0.91rem;}
  .hb-table th {background:#f3f7fb;color:#274a66;}
  .hb-table td.num,.hb-table th.num {text-align:right;font-variant-numeric:tabular-nums;}
  .hb-scroll {overflow-x:auto;}
  .hb-note {padding:14px 18px;border:1px solid #d9e2eb;border-radius:10px;background:#f6f9fc;line-height:1.65;margin:14px 0 24px;}
  .hb-legend {font-size:.89rem;color:#566879;margin-top:9px;}
  .hb-empty {color:#71531d;padding:14px;background:#fff7e5;border-radius:8px;}
</style>
<div class="page-head"><div>
  <p class="eyebrow">PRIVATE RESEARCH · AGGREGATED DATA</p>
  <h1>Historical billing insights</h1>
  <p class="subheading">Actual masked billing data: aggregate patterns only. Separate from our 200-customer synthetic demo.</p>
</div></div>
<div class="hb-note">
  <strong>Descriptive research, not a validated credit score.</strong>
  A positive outstanding-debt row does not establish default, an unpaid bill, or a suspension.
  The amount fields may be snapshots, and masked account IDs are not joined between years.
  The 2026 source covers only part of the year; 2025 was not supplied.
</div>
{% if not historical_available %}
<div class="hb-empty"><strong>Historical summary not found.</strong> Run <code>python 29_prepare_account_month_features.py</code> in full mode first, then refresh this page. The website remains usable with dummy data.</div>
{% endif %}
<div class="stats-grid">
  <section class="stat-card"><span class="stat-label">Billing records represented</span><strong>{{ '{:,}'.format(total_billing_rows) if historical_available else '—' }}</strong><small>Not necessarily unique invoices</small></section>
  <section class="stat-card"><span class="stat-label">Account-month observations</span><strong>{{ '{:,}'.format(total_account_months) if historical_available else '—' }}</strong><small>Not distinct customers</small></section>
  <section class="stat-card"><span class="stat-label">Years available</span><strong>{{ total_years if historical_available else '—' }}</strong><small>2021–2024 and 2026, if reports available</small></section>
  <section class="stat-card"><span class="stat-label">Account-months with positive outstanding indicator</span><strong>{{ '{:,}'.format(total_positive_account_months) if historical_available else '—' }}</strong><small>Descriptive indicator; not confirmed non-payment</small></section>
</div>
<section class="panel table-panel">
  <div class="panel-title"><div><h2>Historical coverage by year</h2><p>Year-specific account totals must not be added across years to infer unique customers.</p></div></div>
  {% if years %}<div class="hb-scroll"><table class="hb-table">
  <thead><tr><th>Year</th><th class="num">Billing rows</th><th class="num">Account-month rows</th><th class="num">Distinct accounts in year</th><th class="num">Positive outstanding account-months</th><th class="num">Share</th></tr></thead>
  <tbody>{% for r in years %}<tr>
    <td><strong>{{ r.year }}</strong>{% if r.year == '2026' %} <small>(partial)</small>{% endif %}</td>
    <td class="num">{{ '{:,}'.format(r.billing_rows) }}</td>
    <td class="num">{{ '{:,}'.format(r.account_months) }}</td>
    <td class="num">{{ '{:,}'.format(r.distinct_accounts) }}</td>
    <td class="num">{{ '{:,}'.format(r.positive_account_months) }}</td>
    <td class="num">{{ '%.2f'|format(r.positive_share) }}%</td>
  </tr>{% endfor %}</tbody></table></div>
  {% else %}<p class="hb-empty">Run the Step 29 full report to populate annual figures.</p>{% endif %}
</section>
<section class="panel table-panel">
  <div class="panel-title"><div><h2>Monthly activity</h2><p>Select a year to see account-month activity and positive-outstanding indicators.</p></div></div>
  {% if monthly_available %}
  <div class="hb-chips" aria-label="Choose historical year">
    {% for r in years %}<a class="hb-chip{% if r.year == selected_year %} active{% endif %}" href="{{ url_for('historical_billing', year=r.year) }}" {% if r.year == selected_year %}aria-current="page"{% endif %}>{{ r.year }}{% if r.year == '2026' %} · partial{% endif %}</a>{% endfor %}
  </div>
  {% if months %}<div class="hb-scroll"><table class="hb-table">
  <thead><tr><th>Month</th><th class="num">Billing rows</th><th class="num">Account-months</th><th style="min-width:135px">Relative monthly volume</th><th class="num">Positive-outstanding account-months</th><th class="num">Share</th></tr></thead>
  <tbody>{% for m in months %}<tr>
    <td><strong>{{ m.month }}</strong></td><td class="num">{{ '{:,}'.format(m.billing_rows) }}</td>
    <td class="num">{{ '{:,}'.format(m.account_months) }}</td>
    <td><div class="hb-bartrack" role="img" aria-label="Relative account-month volume {{ '%.1f'|format(m.bar_width) }} percent of annual monthly maximum"><div class="hb-barfill" style="width:{{ m.bar_width }}%"></div></div></td>
    <td class="num">{{ '{:,}'.format(m.positive_account_months) }}</td>
    <td class="num">{{ '%.2f'|format(m.positive_share) }}%</td>
  </tr>{% endfor %}</tbody></table></div>
  <p class="hb-legend">Bars compare monthly account-month counts within the selected year. "Positive outstanding" means at least one positive <code>outstanding_debt_amt</code> row in that account-month, not a default label.</p>
  {% else %}<p class="hb-empty">There are no monthly aggregate observations for this year.</p>{% endif %}
  {% else %}<p class="hb-empty">Monthly aggregate CSV is missing. Run <code>python 29_prepare_account_month_features.py</code> in full mode, then refresh.</p>{% endif %}
</section>
<div class="hb-note"><strong>Data and safety note:</strong> This page reads aggregate CSVs from <code>outputs/private_audit</code>, not customer-level records or raw Parquet files. It is intended only for the local, staff-authenticated prototype. Do not deploy this Flask development server publicly or upload historical source data to a public repository. Creditworthiness factors and weights remain provisional pending verified settlement dates and future outcomes.</div>
{% endblock %}
'''

ROUTE = '''# >>> STEP 42 HISTORICAL BILLING DASHBOARD >>>
@app.route('/historical-billing', methods=['GET'])
def historical_billing():
    # Aggregated Step 29 CSVs only; analyst/admin staff access enforced by staff_auth.
    from historical_billing_dashboard import historical_context as _step42_historical_context
    return render_template('historical_billing.html', active='historical',
                           **_step42_historical_context(ROOT, request.args.get('year', '')))
# <<< STEP 42 HISTORICAL BILLING DASHBOARD <<<

'''

NAV = '''        {% if staff_identity and staff_identity.role in ['admin','analyst'] %}
        <a class="nav-link {% if active == 'historical' %}selected{% endif %}" href="{{ url_for('historical_billing') }}"><span class="nav-icon">▥</span> Historical billing</a>
        {% endif %}
'''


def replace_exact(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f'{label}: Expected exactly one insertion anchor; found {count}. No files changed.')
    return text.replace(old, new, 1)


def main() -> None:
    required = [APP, BASE, AUTH]
    missing = [p for p in required if not p.is_file()]
    if missing:
        raise SystemExit('Could not find existing Flask site / Step 18 staff authentication:\n' +
                         '\n'.join(str(p.relative_to(ROOT)) for p in missing))

    app_text = APP.read_text(encoding='utf-8')
    base_text = BASE.read_text(encoding='utf-8')
    auth_text = AUTH.read_text(encoding='utf-8')

    if MARKER in app_text:
        print('Step 42 already installed. No changes made.')
        print('Open: http://127.0.0.1:5000/historical-billing')
        return

    if '# >>> STEP 18 STAFF AUTH >>>' not in app_text or 'def _permission_guard():' not in auth_text:
        raise SystemExit('Step 18 staff login is required before installing historical pages. No changes made.')

    try:
        app_new = replace_exact(app_text, 'if __name__ == "__main__":',
                                ROUTE + 'if __name__ == "__main__":', 'app.py')
        base_new = replace_exact(base_text, '      </nav>', NAV + '      </nav>', 'base.html')
        auth_new = replace_exact(auth_text,
                                 'endpoint in {"customers", "customer_profile", "creditworthiness", "collections"}',
                                 'endpoint in {"customers", "customer_profile", "creditworthiness", "collections", "historical_billing"}',
                                 'staff_auth.py')
        # Avoid false claims that the whole site shows synthetic records only.
        base_new = base_new.replace('Local dummy-data environment',
                                    'Local demo + private aggregate research')
        base_new = base_new.replace('· Synthetic dataset ·',
                                    '· Synthetic customer demo + private aggregate research ·')
        ast.parse(app_new, filename=str(APP))
        ast.parse(auth_new, filename=str(AUTH))
        ast.parse(VIEW_SOURCE, filename=str(VIEW))
        if VIEW.exists() or TEMPLATE.exists():
            raise ValueError('Step 42 dashboard files already exist, refusing to overwrite. No changes made.')
    except (SyntaxError, ValueError) as exc:
        raise SystemExit(f'Step 42 preflight failed: {exc}')

    originals = [(APP, app_text), (BASE, base_text), (AUTH, auth_text)]
    backup_paths = [p.with_name(p.name + '.step42.bak') for p, _ in originals]
    pre_existing_backups = [p for p in backup_paths if p.exists()]
    if pre_existing_backups:
        raise SystemExit('Backup already exists (no changes made): ' + ', '.join(str(p) for p in pre_existing_backups))

    try:
        for (path, _), backup in zip(originals, backup_paths):
            shutil.copy2(path, backup)
        VIEW.write_text(VIEW_SOURCE, encoding='utf-8')
        TEMPLATE.write_text(TEMPLATE_SOURCE, encoding='utf-8')
        for path, new_text in [(APP, app_new), (BASE, base_new), (AUTH, auth_new)]:
            path.write_text(new_text, encoding='utf-8')
    except Exception:
        for path, original in originals:
            path.write_text(original, encoding='utf-8')
        for path in [VIEW, TEMPLATE]:
            if path.exists():
                path.unlink()
        raise

    # Do not accidentally publish SLT masked billing files / private analysis.
    ignore = GITIGNORE.read_text(encoding='utf-8') if GITIGNORE.exists() else ''
    new_rules = [rule for rule in ('data/historical/', 'outputs/private_audit/') if rule not in ignore]
    if new_rules:
        with GITIGNORE.open('a', encoding='utf-8') as f:
            f.write(('\n' if ignore and not ignore.endswith('\n') else '') +
                    '# Private SLT historical billing data and analysis (Step 42)\n' +
                    ''.join(rule + '\n' for rule in new_rules))

    print('\nSTEP 42 SUCCESS — Historical billing dashboard added to existing Flask website.')
    print('Updated: web_app/app.py, web_app/staff_auth.py, web_app/templates/base.html')
    print('Added: web_app/historical_billing_dashboard.py, web_app/templates/historical_billing.html')
    print('Backups: *.step42.bak')
    print('Only analyst/admin staff may access the new aggregate dashboard.')
    print('No creditworthiness weights, customer records, or existing demo routes were changed.')
    print('Restart: python web_app\\app.py')
    print('Open: http://127.0.0.1:5000/historical-billing')
    print('If Step 29 full reports are missing, this page will show a safe empty state.')


if __name__ == '__main__':
    main()
