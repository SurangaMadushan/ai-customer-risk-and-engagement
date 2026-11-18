"""Step 19: scoped, expiring LOCAL DEMO disclosure previews.

This uses Step 17's simulated approval and Step 18's local staff login.
It is NOT real customer consent, an SMS system, or production authorization.
The existing internal customer pages are NOT gated by this demo workflow.
"""
from __future__ import annotations

import re
import time
from contextlib import closing

import pandas as pd
from flask import Blueprint, abort, current_app, render_template

from consent_demo import VALID_SCOPES, db_conn
from staff_auth import current_staff

bp = Blueprint("consent_access", __name__)
REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _audit(request_id: str, staff_username: str, account_no: str,
           scope: str, outcome: str, reason: str) -> None:
    """Write local read/deny audit entry; no actual disclosures outside this site."""
    with closing(db_conn()) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS consent_access_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                time_ts INTEGER NOT NULL,
                request_id TEXT NOT NULL,
                staff_username TEXT NOT NULL,
                account_no TEXT NOT NULL,
                data_scope TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason TEXT NOT NULL
            )
        """)
        con.execute("""
            INSERT INTO consent_access_log
            (time_ts, request_id, staff_username, account_no, data_scope, outcome, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (int(time.time()), request_id, staff_username, account_no, scope, outcome, reason))
        con.commit()


def _get_record(request_id: str):
    with closing(db_conn()) as con:
        row = con.execute("""
            SELECT request_id, account_no, requester, purpose, data_scope,
                   status, decided_ts, expires_ts
            FROM consent_requests WHERE request_id=?
        """, (request_id,)).fetchone()
        return dict(row) if row is not None else None


def _format_money(n) -> str:
    return f"LKR {float(n):,.2f}"


def _safe_number(value) -> float:
    """Convert incomplete dummy data to zero without NaN/NA errors."""
    try:
        number = float(value)
        return number if pd.notna(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _scope_fields(profile: dict, sheets: dict, scope: str, account_no: str):
    """Provide an allowlist per consent scope; do not render the full profile."""
    if scope == "Account summary":
        return [
            ("Customer ID", str(profile.get("customer_id", "—"))),
            ("Account number", account_no),
            ("Customer type", str(profile.get("customer_type", "—"))),
            ("Service type", str(profile.get("service_type", "—"))),
            ("Package", str(profile.get("package", "—"))),
            ("Region", str(profile.get("region", "—"))),
        ]
    if scope == "Billing summary":
        bills = sheets["Billing"]
        subset = bills.loc[bills["account_no"].eq(account_no)]
        totals = pd.to_numeric(subset.get("invoice_net_amt", pd.Series(dtype=float)), errors="coerce")
        return [
            ("Account number", account_no),
            ("Number of recorded invoices", str(len(subset))),
            ("Total invoiced net amount (recorded history)", _format_money(totals.fillna(0).sum())),
        ]
    if scope == "Payment status":
        days = int(_safe_number(profile.get("current_overdue_days")))
        past_due = int(_safe_number(profile.get("unpaid_past_due_bills")))
        outstanding = _safe_number(profile.get("current_outstanding_LKR"))
        return [
            ("Account number", account_no),
            ("Current outstanding", _format_money(outstanding)),
            ("Current overdue days", str(days)),
            ("Unpaid past-due bills", str(past_due)),
            ("Currently overdue", "Yes" if days > 0 and past_due > 0 and float(outstanding) > 0 else "No"),
        ]
    raise ValueError("Unrecognized scope")


@bp.get("/consent-demo/disclosure/<request_id>")
def disclosure(request_id: str):
    # Step 18 guards this route before it executes; recheck for defense in depth.
    identity = current_staff()
    if not identity or identity["role"] not in {"admin", "analyst"}:
        abort(403)
    if not REQUEST_ID_PATTERN.fullmatch(request_id):
        abort(404)
    row = _get_record(request_id)
    if row is None:
        abort(404)

    staff_username = identity["username"]
    reason = None
    if row["requester"] != staff_username:
        reason = "Requester does not match signed-in staff"
    elif row["status"] != "Approved" or row["decided_ts"] is None:
        reason = "Request is not approved"
    elif int(time.time()) > int(row["expires_ts"]):
        reason = "Approval expired"
    elif row["data_scope"] not in VALID_SCOPES:
        reason = "Unsupported disclosure scope"
    if reason:
        _audit(request_id, staff_username, row["account_no"], row["data_scope"], "Denied", reason)
        abort(403, "Access denied: request must belong to you and be approved, in scope and unexpired.")

    provider = current_app.extensions.get("slt_step19_data_context")
    if not callable(provider):
        abort(503, "Local data provider unavailable")
    state, missing, error = provider()
    if state is None:
        abort(503, "Dummy dataset is unavailable")
    account_no = row["account_no"]
    matching = state["profiles"].loc[state["profiles"]["account_no"].eq(account_no)]
    if len(matching) != 1:
        abort(404)
    fields = _scope_fields(matching.iloc[0].to_dict(), state["sheets"], row["data_scope"], account_no)
    _audit(request_id, staff_username, account_no, row["data_scope"], "Allowed", "Valid local simulated approval")
    return render_template("consent_disclosure.html", active="consent_demo", consent=row, fields=fields)
