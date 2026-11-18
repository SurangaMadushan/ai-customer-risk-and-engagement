#!/usr/bin/env python3
"""SLT Step 37 — AUDIT the existing PROTOTYPE score's sensitivity to weights.

This is NOT statistical validation of payment outcomes, not training, and does
not make/approve SLT scoring weights. It uses the existing dummy-data Step 15
score components solely to measure how assumptions affect rankings and bands.

Expected existing input (from your original Flask/creditworthiness prototype):
    outputs/customer_creditworthiness.csv

Run from your SLT_Payment_Project directory:
    python 37_audit_creditworthiness_weight_sensitivity.py --sample
    python 37_audit_creditworthiness_weight_sensitivity.py

If your CSV is in another folder:
    python 37_audit_creditworthiness_weight_sensitivity.py --input "PATH\\customer_creditworthiness.csv"

Reports stay in outputs/private_audit. No individual account identifiers or
per-customer scores are exported. Do not push private outputs to public GitHub.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'outputs' / 'private_audit'
FACTORS = [
    ('Settled bill history', 'points_settlement', 25.0),
    ('Payment consistency', 'points_consistency', 20.0),
    ('Average payment delay', 'points_delay', 15.0),
    ('Current arrears', 'points_current_arrears', 25.0),
    ('Outstanding exposure', 'points_balance_exposure', 10.0),
    ('Collection history', 'points_collection_history', 5.0),
]
COLUMNS = [
    'scenario', 'changed_factor', 'weight_change_requested', 'weights_used',
    'valid_accounts', 'avg_baseline_score', 'avg_scenario_score',
    'mean_abs_score_change', 'p95_abs_score_change',
    'accounts_changed_band', 'pct_accounts_changed_band',
    'spearman_rank_correlation', 'lowest_score_top_10pct_overlap',
    'note',
]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def band(score: float) -> str:
    if score >= 80:
        return 'Strong'
    if score >= 60:
        return 'Moderate'
    if score >= 40:
        return 'Low'
    return 'Very Low'


def redistribute(index: int, new_weight: float) -> list[float]:
    """Change one weight, distribute remaining points proportionally to others."""
    base = [row[2] for row in FACTORS]
    if not 0 <= new_weight <= 100:
        raise ValueError('Requested new weight out of bounds')
    others = sum(base) - base[index]
    updated = [b * (100 - new_weight) / others for b in base]
    updated[index] = new_weight
    assert abs(sum(updated) - 100) < 1e-8
    return updated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sample', action='store_true', help='Deterministic sample of 50 eligible demo accounts')
    ap.add_argument('--input', type=Path, help='Step 15 customer_creditworthiness.csv path')
    ap.add_argument('--perturb', type=float, default=5.0, help='Plus/minus points per factor (default: 5)')
    args = ap.parse_args()
    if not 0 < args.perturb <= 10:
        ap.error('--perturb must be > 0 and <= 10 points')

    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        print('ERROR: Install requirements in your .venv: python -m pip install pandas numpy')
        return 1

    input_path = args.input if args.input is not None else ROOT / 'outputs' / 'customer_creditworthiness.csv'
    if not input_path.exists():
        print('\nINPUT NOT FOUND:', input_path)
        print('Step 37 audits the 200-customer PROTOTYPE score, not the Step 32 historical database.')
        print('Run your Step 15 creditworthiness script to generate outputs/customer_creditworthiness.csv.')
        print('Alternatively provide --input "full_path\\customer_creditworthiness.csv"')
        print('No fake scores were generated.')
        return 1

    mode = 'sample' if args.sample else 'full'
    print('\nSLT STEP 37 — PROTOTYPE CREDITWORTHINESS WEIGHT SENSITIVITY')
    print('Mode:', mode.upper())
    print('Input:', input_path)
    print('NOTE: Synthetic prototype score sensitivity; NOT predictive validation or real SLT results.')
    print('No customer IDs or individual scores will be exported.')
    frame = pd.read_csv(input_path, low_memory=False)
    required = [row[1] for row in FACTORS] + ['payment_reliability_score']
    missing = [name for name in required if name not in frame.columns]
    if missing:
        print('ERROR: Step 15 component columns missing:', missing)
        print('Regenerate the score CSV with 15_creditworthiness_analysis.py from your Flask prototype.')
        return 1

    original_count = len(frame)
    for col in required:
        frame[col] = pd.to_numeric(frame[col], errors='coerce')
    # An account with fewer than 3 settled bills has no validated-in-prototype
    # score and is excluded (do not mark as low-creditworthiness).
    eligible = frame.dropna(subset=required).copy()
    if 'settled_bills' in eligible.columns:
        eligible['settled_bills'] = pd.to_numeric(eligible['settled_bills'], errors='coerce')
        eligible = eligible.loc[eligible['settled_bills'] >= 3].copy()
    for name, col, max_w in FACTORS:
        good = np.isfinite(eligible[col]) & (eligible[col] >= -0.011) & (eligible[col] <= max_w + 0.011)
        eligible = eligible.loc[good].copy()
    if args.sample:
        eligible = eligible.sample(n=min(50, len(eligible)), random_state=42)
    if len(eligible) < 2:
        print('ERROR: At least two scored, eligible accounts are needed for sensitivity analysis.')
        return 1

    columns = [x[1] for x in FACTORS]
    weights_base = np.array([x[2] for x in FACTORS], dtype=float)
    quality = (eligible[columns].to_numpy(dtype=float) / weights_base).clip(0, 1)
    baseline = np.round(quality @ weights_base, 1)
    supplied = eligible['payment_reliability_score'].to_numpy(dtype=float)
    discrepancies = int(np.count_nonzero(abs(supplied - baseline) > 0.15))
    if discrepancies:
        print('WARNING: Input score differs from sum of components for', discrepancies, 'accounts.')
        print('We audit component-based recomputed scores, not the inconsistent supplied total.')

    base_bands = np.array([band(s) for s in baseline])
    k = max(1, int(math.ceil(len(eligible) * 0.10)))
    base_risk_indices = set(np.argsort(baseline, kind='stable')[:k].tolist())

    scenarios = [('BASELINE', None, weights_base.tolist(), 'Original six rule-based prototype weights')]
    for i, (name, _, old_w) in enumerate(FACTORS):
        for sign, label in ((1, '+'), (-1, '-')):
            new_w = old_w + sign * args.perturb
            if 0 <= new_w <= 100:
                scenarios.append((f'{name} {label}{args.perturb:g} points', name, redistribute(i, new_w),
                                  'Other five weights rescaled proportionally; scenario only'))
        scenarios.append((f'Remove {name}', name, redistribute(i, 0),
                          'Factor removed and its points redistributed; diagnostic only'))
    scenarios.append(('Equal six-factor weights', 'All', [100 / 6] * 6,
                      'Hypothetical equal-weight benchmark; not a recommendation'))

    rows = []
    for label, factor, weights, note in scenarios:
        modified = np.round(quality @ np.array(weights, dtype=float), 1)
        absdiff = abs(modified - baseline)
        new_bands = np.array([band(s) for s in modified])
        changed = int(np.sum(base_bands != new_bands))
        alt_risk_indices = set(np.argsort(modified, kind='stable')[:k].tolist())
        try:
            sr = float(pd.Series(baseline).corr(pd.Series(modified), method='spearman'))
            sr_value = round(sr, 5) if math.isfinite(sr) else None
        except Exception:
            sr_value = None
        rows.append({
            'scenario': label,
            'changed_factor': factor or 'None',
            'weight_change_requested': args.perturb if factor not in (None, 'All') else '',
            'weights_used': '; '.join(f'{x[0]}={w:.3f}' for x, w in zip(FACTORS, weights)),
            'valid_accounts': len(eligible),
            'avg_baseline_score': round(float(np.mean(baseline)), 2),
            'avg_scenario_score': round(float(np.mean(modified)), 2),
            'mean_abs_score_change': round(float(np.mean(absdiff)), 3),
            'p95_abs_score_change': round(float(np.percentile(absdiff, 95)), 3),
            'accounts_changed_band': changed,
            'pct_accounts_changed_band': round(100 * changed / len(eligible), 2),
            'spearman_rank_correlation': sr_value,
            'lowest_score_top_10pct_overlap': round(len(base_risk_indices & alt_risk_indices) / k, 3),
            'note': note,
        })

    OUT.mkdir(parents=True, exist_ok=True)
    out_csv = OUT / f'step37_weight_sensitivity_{mode}.csv'
    out_json = OUT / f'step37_weight_sensitivity_{mode}.json'
    out_md = OUT / f'step37_weight_sensitivity_interpretation_{mode}.md'
    write_csv(out_csv, rows)
    results = {
        'step': 37,
        'mode': mode,
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'source': str(input_path),
        'input_accounts': original_count,
        'eligible_accounts': len(eligible),
        'excluded_or_unscorable_accounts': original_count - len(eligible) if not args.sample else None,
        'input_vs_component_score_discrepancies': discrepancies,
        'scenario_count': len(rows),
        'labels_available': False,
        'weights_statistically_validated': False,
        'real_slt_performance_evaluated': False,
        'scoring_weights_for_production_approved': False,
    }
    out_json.write_text(json.dumps(results, indent=2), encoding='utf-8')

    ranked = sorted([r for r in rows if r['scenario'] != 'BASELINE'],
                    key=lambda r: r['pct_accounts_changed_band'], reverse=True)
    md = [
        '# Step 37 — Prototype weight sensitivity analysis', '',
        '**Status: SENSITIVITY TEST ONLY — weights NOT statistically validated.**', '',
        f'- Input accounts: {original_count}',
        f'- Eligible scored accounts analyzed: {len(eligible)}',
        f'- Scenarios: {len(rows)} (plus/minus {args.perturb:g} points, removing each factor, equal weights)',
        f'- Prototype score discrepancies: {discrepancies}', '',
        '## Scenarios with greatest score-band changes', '',
        '| Weight scenario | Accounts changing band | Share changing band | Lowest-score top 10% overlap |',
        '|---|---:|---:|---:|',
    ]
    for r in ranked[:8]:
        md.append(f"| {r['scenario']} | {r['accounts_changed_band']} | "
                  f"{r['pct_accounts_changed_band']}% | {r['lowest_score_top_10pct_overlap']:.1%} |")
    md += [
        '', '## How to interpret this', '',
        '- Large score/band changes mean the prototype is **sensitive to arbitrary weights**.',
        '- Small changes mean **stability**, NOT accuracy or predictive validity.',
        '- This file uses the existing synthetic-data prototype creditworthiness component scores.',
        '- No unpaid-by-next-month outcome is linked, so weights CANNOT be judged by accuracy/recall.',
        '- No new weights were adopted; existing Flask score remains provisional.',
        '- The six factor inputs must be available as-of the prediction date before real scoring.',
        '', '## What is still needed for statistical justification', '',
        '1. Locate `bill_settled_dtm` or an invoice-linked, dated payment-event dataset.',
        '2. Confirm business definition of one-month-ahead non-payment, including due-date and cutoff.',
        '3. Confirm time-stamped historical values rather than retrospectively updated balances.',
        '4. Train an interpretable model on real outcomes, evaluate on later time periods, calibrate probabilities.',
        '5. Review feature importance and score methodology with SLT before operational use.',
        '', '**Privacy:** Reports contain aggregate numbers only; do not publish internal outputs.',
    ]
    out_md.write_text('\n'.join(md), encoding='utf-8')

    print('\nScored accounts included:', f'{len(eligible):,}')
    print('Sensitivity scenarios:', len(rows))
    print('Most sensitive scenarios (band changes):')
    for r in ranked[:5]:
        print(f"  {r['scenario']}: {r['accounts_changed_band']} "
              f"({r['pct_accounts_changed_band']}%) changed band")
    print('\nSaved reports:')
    for item in (out_csv, out_json, out_md):
        print(' ', item.relative_to(ROOT))
    print('RESULT: Sensitivity analysis completed; original scoring weights remain UNVALIDATED.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
