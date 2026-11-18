"""LOCAL ONLY: simulated customer consent & OTP flow for SLT dummy data.

Not production authentication or access control. Does NOT grant access to
customer profiles, billing, payments or any real information.
"""
from __future__ import annotations

import csv
import hashlib
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for
from staff_auth import current_staff as _step19_current_staff

bp = Blueprint('consent_demo', __name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB = PROJECT_ROOT / 'data' / 'consent_demo.sqlite3'
CREDIT_CSV = PROJECT_ROOT / 'outputs' / 'customer_creditworthiness.csv'
VALID_SCOPES = {'Account summary', 'Billing summary', 'Payment status'}
MAX_ATTEMPTS = 3
TTL_SECONDS = 300


def db_conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS consent_requests (
        request_id TEXT PRIMARY KEY,
        account_no TEXT NOT NULL,
        requester TEXT NOT NULL,
        purpose TEXT NOT NULL,
        data_scope TEXT NOT NULL,
        otp_hash TEXT NOT NULL,
        otp_salt TEXT NOT NULL,
        created_ts INTEGER NOT NULL,
        expires_ts INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'Pending',
        decided_ts INTEGER
    )''')
    db.commit()
    return db


def hash_otp(code: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac('sha256', code.encode('ascii'), bytes.fromhex(salt), 120_000).hex()


def csrf_token() -> str:
    if 'consent_csrf' not in session:
        session['consent_csrf'] = secrets.token_urlsafe(32)
    return session['consent_csrf']


def verify_csrf():
    supplied = request.form.get('csrf_token', '')
    if not supplied or not session.get('consent_csrf') or not secrets.compare_digest(supplied, session['consent_csrf']):
        abort(400, 'Invalid form token')


def known_accounts() -> set[str]:
    if not CREDIT_CSV.is_file():
        return set()
    with CREDIT_CSV.open('r', encoding='utf-8-sig', newline='') as f:
        return {str(row['account_no']).strip().removesuffix('.0') for row in csv.DictReader(f)}


def display_status(row, now=None) -> str:
    now = int(time.time()) if now is None else now
    if row['status'] == 'Pending' and now > row['expires_ts']:
        return 'Expired'
    return row['status']


@bp.route('/consent-demo', methods=['GET'])
def index():
    csrf_token()
    with db_conn() as conn:
        recent = [dict(x) for x in conn.execute('''SELECT request_id, account_no, requester,
                    purpose, data_scope, created_ts, expires_ts, status, attempts
                    FROM consent_requests WHERE requester=? ORDER BY created_ts DESC LIMIT 25''',
                    (_step19_current_staff()['username'],)).fetchall()]
    for r in recent:
        r['status'] = display_status(r)
    return render_template('consent_demo.html', active='consent_demo',
                           recent=recent, allowed_scopes=sorted(VALID_SCOPES),
                           csrf=csrf_token(), detail=None, demo_code=None,
                           message=request.args.get('message', '')[:120], now=int(time.time()))


@bp.post('/consent-demo/request')
def create_request():
    verify_csrf()
    account = request.form.get('account_no', '').strip()
    # Requester is bound to the authenticated staff session, not a form field.
    staff = _step19_current_staff()
    requester = staff['username'] if staff else ''
    purpose = request.form.get('purpose', '').strip()[:180]
    scope = request.form.get('data_scope', '').strip()
    if (not account.isdigit() or len(account) > 18 or account not in known_accounts()
            or not (2 <= len(requester) <= 70) or not (5 <= len(purpose) <= 180)
            or scope not in VALID_SCOPES):
        return redirect(url_for('consent_demo.index', message='Check the account and form fields.'))
    code = f'{secrets.randbelow(1_000_000):06d}'
    salt = secrets.token_hex(16)
    req_id = uuid.uuid4().hex
    now = int(time.time())
    with db_conn() as conn:
        conn.execute('''INSERT INTO consent_requests
            (request_id, account_no, requester, purpose, data_scope,
             otp_hash, otp_salt, created_ts, expires_ts)
             VALUES (?,?,?,?,?,?,?,?,?)''',
             (req_id, account, requester, purpose, scope, hash_otp(code, salt), salt,
              now, now + TTL_SECONDS))
        conn.commit()
    # Deliberately display only in the LOCAL SIMULATOR. This is NOT an SMS.
    session['demo_otp_' + req_id] = code
    return redirect(url_for('consent_demo.request_detail', request_id=req_id))


@bp.route('/consent-demo/request/<request_id>', methods=['GET'])
def request_detail(request_id: str):
    if len(request_id) != 32 or not all(ch in '0123456789abcdef' for ch in request_id):
        abort(404)
    csrf_token()
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM consent_requests WHERE request_id=?', (request_id,)).fetchone()
    if row is None:
        abort(404)
    if row['requester'] != _step19_current_staff()['username']:
        abort(403)
    detail = dict(row)
    detail['status'] = display_status(detail)
    detail.pop('otp_hash', None)
    detail.pop('otp_salt', None)
    # Code is shown only for simulator convenience; no authenticated customer channel exists.
    demo_code = session.pop('demo_otp_' + request_id, None)
    return render_template('consent_demo.html', active='consent_demo',
                           recent=[], allowed_scopes=sorted(VALID_SCOPES),
                           csrf=csrf_token(), detail=detail, demo_code=demo_code,
                           message=request.args.get('message', '')[:120], now=int(time.time()))


@bp.post('/consent-demo/request/<request_id>/decide')
def decide(request_id: str):
    verify_csrf()
    code = request.form.get('otp', '').strip()
    decision = request.form.get('decision', '')
    if decision not in {'Approved', 'Rejected'} or len(code) != 6 or not code.isdigit():
        return redirect(url_for('consent_demo.request_detail', request_id=request_id, message='Invalid request.'))
    now = int(time.time())
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM consent_requests WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            abort(404)
        if row['requester'] != _step19_current_staff()['username']:
            abort(403)
        if display_status(row, now) != 'Pending' or row['attempts'] >= MAX_ATTEMPTS:
            return redirect(url_for('consent_demo.request_detail', request_id=request_id,
                                    message='Request expired, locked, or already decided.'))
        match = secrets.compare_digest(hash_otp(code, row['otp_salt']), row['otp_hash'])
        if not match:
            attempts = row['attempts'] + 1
            conn.execute('UPDATE consent_requests SET attempts=?, status=? WHERE request_id=?',
                         (attempts, 'Locked' if attempts >= MAX_ATTEMPTS else 'Pending', request_id))
            conn.commit()
            return redirect(url_for('consent_demo.request_detail', request_id=request_id,
                                    message='Incorrect simulated OTP.'))
        conn.execute('UPDATE consent_requests SET status=?, decided_ts=?, attempts=? WHERE request_id=?',
                     (decision, now, row['attempts'] + 1, request_id))
        conn.commit()
    return redirect(url_for('consent_demo.request_detail', request_id=request_id,
                            message=f'Demo decision recorded: {decision}.'))
