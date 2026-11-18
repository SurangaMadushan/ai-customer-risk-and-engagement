"""Step 18: local staff authentication and role checks for the SLT dummy-data website.

Prototype security only: no identity provider, MFA, central audit, HTTPS, or real customer consent.
Do not use with real customer data or publish this Flask development server.
"""
from __future__ import annotations

import secrets
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from contextlib import closing

from flask import (
    Blueprint, abort, current_app, g, redirect, render_template,
    request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "staff_auth.sqlite3"
SECRET_PATH = ROOT / "data" / "staff_auth_secret"
ROLES = ("admin", "analyst", "viewer")
MAX_FAILURES = 5
LOCK_SECONDS = 300

staff_bp = Blueprint("staff_auth", __name__)


def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def initialize_database():
    with closing(connect_db()) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS staff_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin','analyst','viewer')),
                is_active INTEGER NOT NULL DEFAULT 1,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)
        con.commit()


def add_staff_user(username: str, password: str, role: str):
    """Create a test account. Password is hashed; no plaintext stored."""
    username = username.strip().lower()
    if not (3 <= len(username) <= 32) or not all(
        ch.isascii() and (ch.isalnum() or ch in "_.-") for ch in username
    ):
        raise ValueError("Username must be 3-32 letters, digits, underscores, dots or hyphens.")
    if role not in ROLES:
        raise ValueError("Role must be one of: admin, analyst, viewer.")
    if len(password) < 12:
        raise ValueError("Use a password of at least 12 characters.")
    initialize_database()
    with closing(connect_db()) as con:
        try:
            con.execute("""
                INSERT INTO staff_users (username, password_hash, role, created_at)
                VALUES (?, ?, ?, ?)
            """, (username, generate_password_hash(password, method="scrypt"),
                  role, int(time.time())))
            con.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("That username already exists; choose a different username.") from exc


def _load_user(username: str):
    with closing(connect_db()) as con:
        row = con.execute(
            "SELECT username, role, is_active FROM staff_users WHERE username=?",
            (username,),
        ).fetchone()
        return dict(row) if row else None


def current_staff():
    if hasattr(g, "staff_identity"):
        return g.staff_identity
    username = session.get("staff_username", "")
    identity = _load_user(username) if username else None
    if not identity or not identity["is_active"]:
        if username:
            session.clear()
        identity = None
    g.staff_identity = identity
    return identity


def form_csrf() -> str:
    token = session.get("staff_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["staff_csrf"] = token
    return token


def verify_form_csrf():
    supplied = request.form.get("csrf_token", "")
    token = session.get("staff_csrf", "")
    if not supplied or not token or not secrets.compare_digest(supplied, token):
        abort(400, "Invalid form token. Refresh the page and try again.")


def authenticate(username: str, password: str) -> bool:
    now = int(time.time())
    with closing(connect_db()) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM staff_users WHERE username=?", (username,)).fetchone()
        if row is None or not row["is_active"] or row["locked_until"] > now:
            con.commit()
            return False
        valid = check_password_hash(row["password_hash"], password)
        if not valid:
            failures = int(row["failed_attempts"]) + 1
            until = now + LOCK_SECONDS if failures >= MAX_FAILURES else 0
            con.execute("UPDATE staff_users SET failed_attempts=?, locked_until=? WHERE username=?",
                        (failures, until, username))
            con.commit()
            return False
        con.execute("UPDATE staff_users SET failed_attempts=0, locked_until=0 WHERE username=?",
                    (username,))
        con.commit()
        return True


@staff_bp.route("/staff-login", methods=["GET", "POST"])
def login():
    if current_staff() is not None:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        verify_form_csrf()
        username = request.form.get("username", "").strip().lower()[:100]
        password = request.form.get("password", "")[:256]
        if authenticate(username, password):
            session.clear()  # Rotate session contents after login.
            session["staff_username"] = username
            session.permanent = True
            form_csrf()
            return redirect(url_for("index"))
        error = "Incorrect credentials or temporarily locked account."
    return render_template("staff_login.html", error=error, csrf_token=form_csrf())


@staff_bp.route("/staff-logout", methods=["POST"])
def logout():
    verify_form_csrf()
    session.clear()
    return redirect(url_for("staff_auth.login"))


def _permission_guard():
    # Static CSS/JS and the sign-in page must work before authentication.
    endpoint = request.endpoint
    if endpoint in {"static", "staff_auth.login"}:
        return None

    identity = current_staff()
    if identity is None:
        return redirect(url_for("staff_auth.login"))

    if endpoint in {"staff_auth.logout", "index", "validation_status"}:
        return None

    # Only analysts/admins can view individual dummy accounts and analytics.
    if identity["role"] in {"admin", "analyst"} and (
        endpoint in {"customers", "customer_profile", "creditworthiness", "collections", "historical_billing", "risk_prediction", "usage_analytics", "credit_data"}
        or (endpoint is not None and (endpoint.startswith("consent_demo.") or endpoint.startswith("consent_access.")))
    ):
        return None

    if endpoint is None:
        abort(404)
    abort(403, "Your account does not have access to this page.")


def _staff_template_context():
    return {"staff_identity": current_staff(), "staff_logout_csrf": form_csrf()}


def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    # Avoid caching authenticated prototype screens and account information.
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


def _persistent_session_secret() -> str:
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not SECRET_PATH.exists():
        value = secrets.token_hex(32)
        try:
            with SECRET_PATH.open("x", encoding="utf-8") as f:
                f.write(value)
        except FileExistsError:
            pass
    secret = SECRET_PATH.read_text(encoding="utf-8").strip()
    if len(secret) < 64:
        raise RuntimeError("Invalid local session secret. Restore it from a safe backup.")
    return secret


def configure_staff_auth(app):
    """Attach test staff auth without changing existing Flask routes or scoring."""
    initialize_database()
    # Step 17 creates an ephemeral secret; replace it with a stable local one.
    app.secret_key = _persistent_session_secret()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,  # Only for http://127.0.0.1; must be True with HTTPS.
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
    )
    app.register_blueprint(staff_bp)
    app.before_request(_permission_guard)
    app.context_processor(_staff_template_context)
    app.after_request(_security_headers)
