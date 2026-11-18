#!/usr/bin/env python3
"""SLT Step 39: Test a one-month-ahead non-payment label definition safely.

Run from SLT_Payment_Project root:
    python 39_test_one_month_nonpayment_target.py

This script is a synthetic-data TEST HARNESS, not real SLT model training.
It uses built-in Python only; no new dataset or pip installation is needed.

Background:
  * At the end of calendar month T, predict whether an active account will
    have at least one bill DUE in month T+1 that is not fully settled by the
    END of T+1. Requires SLT approval of the cutoff and bill eligibility.
  * Bill-settlement timestamps, invoice IDs, and historical as-of evidence
    were missing from the five historical billing Parquet schemas inspected.
  * Invoice-level "unpaid 30 days AFTER due date" is a DIFFERENT target.
  * A zero payments_amt row is NOT a verified non-payment label.
  * Late-arriving corrections, partial payments, cancellations and credits
    require explicit validation before a real outcome is labelled.

The synthetic examples below deliberately include edge cases. The script
writes only aggregate results and synthetic scenario checks to
outputs/private_audit/step39_*. No private customer records are accessed.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / 'outputs' / 'private_audit'
STEP38 = PRIVATE / 'step38_outcome_source_readiness.json'


@dataclass(frozen=True)
class SyntheticInvoice:
    invoice_id: str
    account: str
    bill_date: date
    due_date: date
    settled_date: date | None
    # A completed source is ASSUMED in this synthetic test, not in the SLT files.
    # Real pipelines must also validate invoice cancellation/adjustment and status.


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def following_month_end(as_of: date) -> date:
    y, m = as_of.year, as_of.month
    if m == 12:
        return month_end(y + 1, 1)
    return month_end(y, m + 1)


def label_one_account(as_of: date, observed_through: date,
                      invoices: list[SyntheticInvoice]) -> str:
    """Return DEMO binary target, NO_ELIGIBLE_BILL or CENSORED.

    A cohort of active accounts at `as_of` must be constructed separately
    from an actual point-in-time customer-master snapshot. The synthetic
    list represents invoices for one such account. Invoice details that
    arose after as_of may be inspected for LABELS ONLY, never as features.
    """
    if as_of != month_end(as_of.year, as_of.month):
        raise ValueError('Prediction cutoff must be the last day of a month')
    outcome_end = following_month_end(as_of)
    if observed_through < outcome_end:
        return 'CENSORED_INCOMPLETE_FOLLOWUP'
    eligible = [inv for inv in invoices
                if as_of < inv.due_date <= outcome_end]
    if not eligible:
        return 'NO_ELIGIBLE_BILL'
    for inv in eligible:
        if inv.bill_date > inv.due_date:
            raise ValueError('Bill date later than due date in synthetic demo')
    # Assumes "settled_date" is the verifiably complete *full settlement*
    # timestamp and absent date means not fully settled as of follow-up end.
    unpaid = any(inv.settled_date is None or inv.settled_date > outcome_end
                 for inv in eligible)
    return '1_UNPAID_BY_NEXT_MONTH_END' if unpaid else '0_SETTLED_BY_NEXT_MONTH_END'


def demo_cases() -> list[dict]:
    jan = date(2024, 1, 31)
    feb = date(2024, 2, 29)  # Leap-year month end.
    def inv(key, customer, issued, due, settled):
        return SyntheticInvoice(key, customer, date.fromisoformat(issued),
                                date.fromisoformat(due),
                                date.fromisoformat(settled) if settled else None)
    return [
        {'case': 'fully_settled_early', 'as_of': jan, 'observed': feb,
         'invoices': [inv('T01','TEST_A','2024-01-19','2024-02-12','2024-02-10')],
         'expected': '0_SETTLED_BY_NEXT_MONTH_END'},
        {'case': 'settled_exactly_on_month_end', 'as_of': jan, 'observed': feb,
         'invoices': [inv('T02','TEST_B','2024-02-02','2024-02-21','2024-02-29')],
         'expected': '0_SETTLED_BY_NEXT_MONTH_END'},
        {'case': 'settled_after_month_end', 'as_of': jan, 'observed': date(2024,3,10),
         'invoices': [inv('T03','TEST_C','2024-01-24','2024-02-08','2024-03-01')],
         'expected': '1_UNPAID_BY_NEXT_MONTH_END'},
        {'case': 'still_unsettled', 'as_of': jan, 'observed': feb,
         'invoices': [inv('T04','TEST_D','2024-01-29','2024-02-15',None)],
         'expected': '1_UNPAID_BY_NEXT_MONTH_END'},
        {'case': 'one_of_two_bills_unsettled', 'as_of': jan, 'observed': feb,
         'invoices': [inv('T05','TEST_E','2024-01-20','2024-02-06','2024-02-02'),
                      inv('T06','TEST_E','2024-02-14','2024-02-28',None)],
         'expected': '1_UNPAID_BY_NEXT_MONTH_END'},
        {'case': 'no_bill_due_next_month', 'as_of': jan, 'observed': feb,
         'invoices': [inv('T07','TEST_F','2024-01-02','2024-01-25',None)],
         'expected': 'NO_ELIGIBLE_BILL'},
        {'case': 'incomplete_followup', 'as_of': jan, 'observed': date(2024,2,15),
         'invoices': [inv('T08','TEST_G','2024-01-27','2024-02-22',None)],
         'expected': 'CENSORED_INCOMPLETE_FOLLOWUP'},
        {'case': 'bill_issued_next_month_but_due_next_month', 'as_of': jan,
         'observed': feb,
         'invoices': [inv('T09','TEST_H','2024-02-01','2024-02-20',None)],
         'expected': '1_UNPAID_BY_NEXT_MONTH_END'},
        {'case': 'december_to_january', 'as_of': date(2023,12,31),
         'observed': date(2024,1,31),
         'invoices': [inv('T10','TEST_I','2023-12-21','2024-01-09','2024-01-10')],
         'expected': '0_SETTLED_BY_NEXT_MONTH_END'},
        {'case': 'old_overdue_not_due_in_target_month', 'as_of': jan,
         'observed': feb,
         'invoices': [inv('T11','TEST_J','2023-12-20','2024-01-15',None)],
         'expected': 'NO_ELIGIBLE_BILL'},
    ]


def check_real_source_readiness() -> tuple[str, dict]:
    if not STEP38.exists():
        return ('STEP38_REPORT_MISSING', {
            'detail': 'Run python 38_verify_payment_outcome_sources.py first.',
        })
    try:
        state = json.loads(STEP38.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return ('STEP38_REPORT_UNREADABLE', {'detail': str(exc)})
    # A schema/header match is NEVER evidence of meaningful, joinable,
    # point-in-time complete settlement history.
    evidence = {
        'step38_inspected_files': state.get('inspected_files', 'unknown'),
        'timestamp_candidates_found': len(state.get('timestamp_candidates_found_in') or []),
        'invoice_key_candidates_found': len(state.get('invoice_id_candidates_found_in') or []),
        'timestamp_and_invoice_key_cooccur_candidates': len(
            state.get('timestamp_and_invoice_id_in_same_file') or []),
        'real_one_month_target_validated': bool(
            state.get('real_one_month_target_validated', False)),
    }
    return 'HOLD_PENDING_VERIFIED_JOINED_SETTLEMENT_DATA', evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-cases', action='store_true',
                        help='Print synthetic scenario names and outcomes (no real data)')
    args = parser.parse_args()
    PRIVATE.mkdir(parents=True, exist_ok=True)
    print('\nSLT STEP 39 — ONE-MONTH LABEL LOGIC: SYNTHETIC TESTS')
    print('Uses synthetic examples only; no customer or billing files are read.')
    print('Prediction at month-end T; outcome = unsettled bill due in T+1 at T+1 end.')
    rows = []
    for case in demo_cases():
        observed = label_one_account(case['as_of'], case['observed'], case['invoices'])
        passed = observed == case['expected']
        rows.append({
            'synthetic_scenario': case['case'],
            'prediction_cutoff': str(case['as_of']),
            'observed_through': str(case['observed']),
            'expected_outcome': case['expected'],
            'actual_outcome': observed,
            'test_status': 'PASS' if passed else 'FAIL',
        })
        if args.show_cases:
            print(f"  {case['case']}: {observed} ({'PASS' if passed else 'FAIL'})")
    checks = Counter(r['test_status'] for r in rows)
    readiness, evidence = check_real_source_readiness()
    report_path = PRIVATE / 'step39_synthetic_label_tests.csv'
    with report_path.open('w', newline='', encoding='utf-8') as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    state = {
        'step': 39,
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'synthetic_test_cases': len(rows),
        'synthetic_test_pass': checks['PASS'],
        'synthetic_test_fail': checks['FAIL'],
        'real_source_readiness': readiness,
        'real_source_evidence': evidence,
        'creditworthiness_weights_validated': False,
        'actual_slt_labels_generated': False,
    }
    json_path = PRIVATE / 'step39_label_logic_and_readiness.json'
    json_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    md_path = PRIVATE / 'step39_target_methodology.md'
    md_path.write_text('\n'.join([
        '# Step 39 — One-month non-payment target: test results and methodology',
        '', f"Synthetic tests: **{checks['PASS']}/{len(rows)} PASS**, {checks['FAIL']} FAIL.",
        f'Real outcome readiness: **{readiness}**.',
        '', '## Proposed target for SLT approval',
        'At the end of month T, estimate whether an eligible active customer account',
        'will have any eligible invoice DUE in month T+1 that is not FULLY settled',
        'by the last day of month T+1. This is a customer-level next-calendar-month target.',
        'It is not the older invoice-level "30 days after due date" target.',
        '', '## Example dates',
        '- Prediction cutoff: 2024-01-31.',
        '- Label period: 2024-02-01 through 2024-02-29.',
        '- A qualifying bill fully settled on 2024-02-29 is labelled 0.',
        '- A qualifying bill fully settled on 2024-03-01 is labelled 1.',
        '- An account with no qualifying invoice is NOT automatically a negative; mark NO_ELIGIBLE_BILL.',
        '- If the source ends before 2024-02-29, mark CENSORED; do not guess.',
        '- Bills issued after T may contribute to the FUTURE label only; they must',
        '  NEVER be included in the account features available at T.',
        '', '## Real-data prerequisites (NOT satisfied by synthetic tests)',
        '1. SLT approval of customer eligibility, label definition, grace period, partial',
        '   payment policy, dispute/cancellation treatment, and minimum overdue amount.',
        '2. Unique, verified invoice link from billing to complete settlement history',
        '   or dated payment transactions (including partial payments, credits).',
        '3. Verified bill_settled_dtm semantics, event effective timestamps, and',
        '   complete historical follow-up for the target period.',
        '4. Stable account ID masking and point-in-time customer-master eligibility.',
        '5. Financial features reconstructed as they were known at T (avoid leakage',
        '   from retrospectively updated amounts).',
        '6. Independent chronological model testing, calibration, and monitoring.',
        '', '## Status',
        '**No real SLT labels, validated default probabilities, scoring weights,',
        'or production credit decisions were generated by Step 39.**',
        'The current 25/20/15/25/10/5 scores remain PROVISIONAL.',
        '', '## Next practical action',
        '- Open outputs/private_audit/step38_data_request_to_madam.txt.',
        '- Ask the SLT data owner for bill_settled_dtm source/schema and a',
        '  verified unique invoice join key, or dated invoice payment ledger.',
        '- Once an approved extract is available, validate joins and historical',
        '  settlement coverage before creating ANY real labels.',
        '- Do not upload internal or customer-level data to public GitHub.',
    ]) + '\n', encoding='utf-8')
    print(f'Synthetic target logic: {checks["PASS"]}/{len(rows)} tests PASS.')
    print('Actual SLT label readiness:', readiness)
    print('No new scoring weights or customer default labels were created.')
    print('Saved:', report_path.relative_to(ROOT))
    print('Saved:', json_path.relative_to(ROOT))
    print('Saved:', md_path.relative_to(ROOT))
    return 0 if checks['FAIL'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
