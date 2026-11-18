#!/usr/bin/env python3
"""SLT Step 41: Add a read-only historical-analysis progress dashboard to the EXISTING Flask site.

Run inside SLT_Payment_Project root:
    python 41_install_model_validation_dashboard.py
    python web_app\\app.py

Requires the Step 18 staff-auth setup to already exist. This installer deliberately
DOES NOT modify creditworthiness calculations, ingest raw customer-level data,
create predictions, or present descriptive checks as predictive model validation.

It backs up existing app.py, base.html, creditworthiness.html and staff_auth.py
before changing them. Re-running this script is safe.
"""
from __future__ import annotations

import ast
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web_app'
APP = WEB / 'app.py'
BASE = WEB / 'templates' / 'base.html'
CREDIT = WEB / 'templates' / 'creditworthiness.html'
AUTH = WEB / 'staff_auth.py'
VIEW = WEB / 'validation_dashboard.py'
TEMPLATE = WEB / 'templates' / 'validation_status.html'
IGNORE = ROOT / '.gitignore'
MARKER = '# >>> STEP 41 MODEL VALIDATION DASHBOARD >>>'

VIEW_SOURCE = '''"""Read only WHITELISTED aggregate Step 29–40 results for the local Flask UI.

Never query private DuckDB account tables, output masked identifiers, or serve raw
reports. Showing a readiness check is NOT statistical validation of predictions.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


def _positive_int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _read_json(path: Path):
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        return obj if isinstance(obj, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None


def dashboard_context(root: Path) -> dict:
    private = root / 'outputs' / 'private_audit'
    historical_file = private / 'step29_yearly_account_month_summary_full.csv'
    historical = []
    if historical_file.is_file():
        try:
            with historical_file.open('r', newline='', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    year = str(row.get('year', '')).strip()
                    if year not in ('2021', '2022', '2023', '2024', '2026'):
                        continue
                    historical.append({
                        'year': year,
                        'bills': _positive_int(row.get('represented_billing_rows')),
                        'months': _positive_int(row.get('account_month_rows')),
                        'accounts': _positive_int(row.get('distinct_accounts')),
                        'debt_months': _positive_int(row.get('account_months_with_positive_debt_row')),
                    })
        except (OSError, UnicodeError, csv.Error):
            historical = []
    # Yearly distinct-account counts are deliberately NOT added together:
    # masked identifiers might or might not be stable between years.
    historical.sort(key=lambda x: x['year'])
    gate = _read_json(private / 'step40_validation_gate.json') or {}
    reports = [
        ('Step 29', 'Historical account-month features', 'step29_yearly_account_month_summary_full.csv'),
        ('Step 30', 'Outstanding-indicator transitions', 'step30_monthly_indicator_transition_counts_full.csv'),
        ('Step 31', 'Historical payment-pattern analysis', 'step31_payment_behaviour_summary_full.csv'),
        ('Step 32', 'Creditworthiness feature preparation', 'step32_feature_dictionary_full.md'),
        ('Step 33', 'Model feature readiness audit', 'step33_model_readiness_full.md'),
        ('Step 34', 'One-month target readiness', 'step34_prediction_target_contract_full.md'),
        ('Step 35', 'Scoring factor evidence review', 'step35_factor_audit_full.json'),
        ('Step 36', 'Statistical feature diagnostics', 'step36_statistical_diagnostics_full.json'),
        ('Step 37', 'Prototype weight sensitivity test', 'step37_weight_sensitivity_full.json'),
        ('Step 38', 'Payment-outcome field inventory', 'step38_outcome_source_readiness.json'),
        ('Step 39', 'Synthetic target-definition tests', 'step39_label_logic_and_readiness.json'),
        ('Step 40', 'Validation decision and handoff', 'step40_validation_gate.json'),
    ]
    # Presence of a report is not proof that a real prediction model was validated.
    completion = [{'step': step, 'task': task, 'present': (private / filename).is_file()}
                  for step, task, filename in reports]
    # Step 31 filename can vary by release; avoid a misleading missing mark.
    for item in completion:
        if item['step'] == 'Step 31' and not item['present']:
            item['present'] = bool(list(private.glob('step31_*_full.csv')))
    # Step 32 names vary by version; accept the feature database when available.
    for item in completion:
        if item['step'] == 'Step 32' and not item['present']:
            item['present'] = (private / 'step32_private_creditworthiness_features_full.duckdb').is_file()

    label_tests = _read_json(private / 'step39_label_logic_and_readiness.json') or {}
    return {
        'historical_available': bool(historical),
        'historical': historical,
        'total_bill_rows': sum(r['bills'] for r in historical),
        'total_account_months': sum(r['months'] for r in historical),
        'years': ', '.join(r['year'] for r in historical) if historical else 'Not available',
        'gate_available': bool(gate),
        'report_present_count': _positive_int(gate.get('reports_found')),
        'report_expected_count': _positive_int(gate.get('reports_expected')),
        'stats_present': (private / 'step36_statistical_diagnostics_full.json').is_file(),
        'sensitivity_present': (private / 'step37_weight_sensitivity_full.json').is_file(),
        'source_inventory_present': (private / 'step38_outcome_source_readiness.json').is_file(),
        'synthetic_tests_passed': label_tests.get('synthetic_test_fail') == 0 and
                                   _positive_int(label_tests.get('synthetic_test_pass')) > 0 and
                                   _positive_int(label_tests.get('synthetic_test_pass')) ==
                                   _positive_int(label_tests.get('synthetic_test_cases')),
        # This UI never grants itself the authority to declare real-data validation.
        'validated_weights': False,
        'production_approved': False,
        'completion': completion,
    }
'''

TEMPLATE_SOURCE = '''{% extends 'base.html' %}
{% block title %}Model validation{% endblock %}
{% block breadcrumb %}Model validation{% endblock %}
{% block content %}
<div class="page-head"><div><p class="eyebrow">HISTORICAL BILLING & EVIDENCE</p><h1>Model validation dashboard</h1><p class="subheading">Our Phase 1 research progress — separate from the 200-customer dummy-data creditworthiness website.</p></div></div>
<div class="notice"><strong>Research status — not production-approved.</strong> Steps 22–40 examine historical billing, feature quality and provisional scoring rules. They have <strong>not</strong> verified a real one-month non-payment model or established statistically justified scoring weights. Existing customer scores remain dummy-data prototype results.</div>
{% if not historical_available %}<div class="notice">Step 29 full summary was not found. Run <code>python 29_prepare_account_month_features.py</code> to generate historical totals.</div>{% endif %}
<div class="stats-grid">
  <section class="stat-card"><span class="stat-label">Historical billing rows</span><strong>{{ '{:,}'.format(total_bill_rows) if historical_available else '—' }}</strong><small>Aggregate only; not unique invoices</small></section>
  <section class="stat-card"><span class="stat-label">Account-month feature rows</span><strong>{{ '{:,}'.format(total_account_months) if historical_available else '—' }}</strong><small>Not unique customers</small></section>
  <section class="stat-card"><span class="stat-label">Historical years</span><strong class="medium-number">{{ years }}</strong><small>2025 intentionally not required; 2026 partial</small></section>
  <section class="stat-card"><span class="stat-label">Predictively validated score</span><strong class="medium-number" style="color:#b74354">Not yet</strong><small>Settlement-date/outcome evidence still needed</small></section>
</div>
<div class="two-col">
  <section class="panel"><div class="panel-title"><div><h2>Work completed</h2><p>Presence of local reports indicates research artifacts, not approved model results</p></div></div>
    <div class="method-grid">
    {% for item in completion %}<div><dt><strong>{{ item.step }}</strong> — {{ item.task }}</dt><dd style="color:{{ '#147a4e' if item.present else '#9b6118' }}">{{ 'Report found' if item.present else 'Check file' }}</dd></div>{% endfor %}
    </div>
  </section>
  <section class="panel"><div class="panel-title"><div><h2>What still needs confirmation</h2><p>Blocking items before scoring weights can be validated</p></div></div>
    <ol class="long-copy" style="padding-left:20px;line-height:2.2">
      <li>Source of <code>bill_settled_dtm</code> or dated payment transactions.</li>
      <li>Reliable invoice-to-payment linkage and stable masked IDs.</li>
      <li>Whether historical amount fields are snapshots or values known at the time.</li>
      <li>Madam's approval of the exact next-month non-payment outcome.</li>
      <li>Independent time-based validation of future outcomes and scoring factors.</li>
    </ol>
    <p class="explain-text"><strong>Current scoring weights:</strong> 25 / 20 / 15 / 25 / 10 / 5 are still provisional. Feature correlations and sensitivity checks alone cannot prove these are the right weights.</p>
  </section>
</div>
{% if historical_available %}
<section class="panel table-panel"><div class="panel-title"><div><h2>Historical account-month preparation by year</h2><p>Non-identifying aggregates from Step 29</p></div></div>
<div class="table-scroll"><table><thead><tr><th>Year</th><th>Billing rows</th><th>Account-month rows</th><th>Accounts within year</th><th>Account-months with outstanding indicator</th></tr></thead><tbody>
{% for row in historical %}<tr><td><strong>{{ row.year }}</strong></td><td>{{ '{:,}'.format(row.bills) }}</td><td>{{ '{:,}'.format(row.months) }}</td><td>{{ '{:,}'.format(row.accounts) }}</td><td>{{ '{:,}'.format(row.debt_months) }}</td></tr>{% endfor %}
</tbody></table></div><p class="explain-text">These are descriptive billing indicators. Do not add annual distinct-account counts to estimate unique customers across years or interpret positive outstanding rows as confirmed defaults.</p></section>
{% endif %}
<section class="panel"><div class="panel-title"><div><h2>Next milestone</h2><p>Move from prototype rules to statistically validated evidence</p></div></div>
<p class="long-copy">Acquire authorized, dated settlement/payment history and validate the join. Then define the one-month target, construct point-in-time labels, evaluate an interpretable model on a later period, and review scoring weights with SLT.</p>
<p style="margin-top:17px"><a class="btn btn-soft" href="{{ url_for('creditworthiness') }}">View existing prototype creditworthiness →</a></p>
</section>
{% endblock %}
'''

ROUTE = '''# >>> STEP 41 MODEL VALIDATION DASHBOARD >>>
@app.route('/validation-status', methods=['GET'])
def validation_status():
    # Only a small whitelist of aggregated progress data is read. Never query raw customer records.
    from validation_dashboard import dashboard_context as _step41_dashboard_context
    return render_template('validation_status.html', active='validation',
                           **_step41_dashboard_context(ROOT))
# <<< STEP 41 MODEL VALIDATION DASHBOARD <<<

'''

NAV = '''        {% if staff_identity %}
        <a class="nav-link {% if active == 'validation' %}selected{% endif %}" href="{{ url_for('validation_status') }}"><span class="nav-icon">▤</span> Model validation</a>
        {% endif %}
'''

NOTICE = '''<div class="notice"><strong>Historical research update:</strong> We have completed billing-data and scoring-method diagnostics. <a href="{{ url_for('validation_status') }}">View Model Validation Dashboard →</a> Our current creditworthiness scores are still based on synthetic data and provisional weights.</div>
'''


def single_replace(source: str, anchor: str, replacement: str, label: str) -> str:
    matches = source.count(anchor)
    if matches != 1:
        raise ValueError(f'{label}: Expected one insertion anchor, found {matches}. Nothing changed.')
    return source.replace(anchor, replacement, 1)


def main() -> None:
    required = [APP, BASE, CREDIT, AUTH]
    missing = [p for p in required if not p.is_file()]
    if missing:
        raise SystemExit('Step 41 requires the existing Flask website and Step 18 login:\n' +
                         '\n'.join(str(p.relative_to(ROOT)) for p in missing))
    app_txt = APP.read_text(encoding='utf-8')
    base_txt = BASE.read_text(encoding='utf-8')
    credit_txt = CREDIT.read_text(encoding='utf-8')
    auth_txt = AUTH.read_text(encoding='utf-8')
    if MARKER in app_txt:
        print('Step 41 is already installed. No changes required.')
        print('Open: http://127.0.0.1:5000/validation-status')
        return
    if 'def _permission_guard():' not in auth_txt or '# >>> STEP 18 STAFF AUTH >>>' not in app_txt:
        raise SystemExit('Expected Step 18 staff login not detected. No changes made.')

    try:
        app_new = single_replace(app_txt, 'if __name__ == "__main__":',
                                 ROUTE + 'if __name__ == "__main__":', 'app.py')
        base_new = single_replace(base_txt, '      </nav>', NAV + '      </nav>', 'base.html')
        credit_new = single_replace(credit_txt, '<div class="two-col">',
                                    NOTICE + '<div class="two-col">', 'creditworthiness.html')
        auth_new = single_replace(auth_txt,
                                  'if endpoint in {"staff_auth.logout", "index"}:',
                                  'if endpoint in {"staff_auth.logout", "index", "validation_status"}:',
                                  'staff_auth.py')
        # Syntax-check EVERY Python file before writing. Avoid any partial install.
        ast.parse(app_new, filename=str(APP))
        ast.parse(auth_new, filename=str(AUTH))
        ast.parse(VIEW_SOURCE, filename=str(VIEW))
        for path in [VIEW, TEMPLATE]:
            if path.exists():
                raise ValueError(f'{path.relative_to(ROOT)} already exists; refusing to overwrite it.')
    except (SyntaxError, ValueError) as exc:
        raise SystemExit(f'Preflight failed: {exc}\nNo files were changed.')

    originals = [(APP, app_txt), (BASE, base_txt), (CREDIT, credit_txt), (AUTH, auth_txt)]
    backups = []
    try:
        for path, _ in originals:
            backup = path.with_name(path.name + '.step41.bak')
            if backup.exists():
                raise ValueError(f'Backup already exists: {backup.name}; refusing to overwrite.')
            shutil.copy2(path, backup)
            backups.append(backup)
        # Additive-only install: no scoring calculation is modified.
        VIEW.write_text(VIEW_SOURCE, encoding='utf-8')
        TEMPLATE.write_text(TEMPLATE_SOURCE, encoding='utf-8')
        for path, content in [(APP, app_new), (BASE, base_new), (CREDIT, credit_new), (AUTH, auth_new)]:
            path.write_text(content, encoding='utf-8')
    except Exception:
        for path, content in originals:
            if path.exists():
                path.write_text(content, encoding='utf-8')
        for path in [VIEW, TEMPLATE]:
            if path.is_file():
                path.unlink()
        raise

    # Ensure local historical audit files never enter a public repository.
    ignore = IGNORE.read_text(encoding='utf-8') if IGNORE.exists() else ''
    if 'outputs/private_audit/' not in ignore:
        with IGNORE.open('a', encoding='utf-8') as f:
            f.write(('\n' if ignore and not ignore.endswith('\n') else '') +
                    '\n# Local masked billing evidence — never push\noutputs/private_audit/\n')

    print('STEP 41 SUCCESS: New Model validation page installed into existing Flask website.')
    print('Existing creditworthiness scores, customer data, and collection rules unchanged.')
    print('Backups created: *.step41.bak')
    print('Restart website: python web_app\\app.py')
    print('Open: http://127.0.0.1:5000/validation-status')
    print('Note: This is a local progress dashboard, not a validated risk model.')


if __name__ == '__main__':
    main()
