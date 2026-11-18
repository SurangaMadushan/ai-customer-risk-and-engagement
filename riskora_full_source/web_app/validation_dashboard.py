"""Read only WHITELISTED aggregate Step 29–40 results for the local Flask UI.

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
