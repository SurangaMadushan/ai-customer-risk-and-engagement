"""Step 19 installer for the existing Step 17 + Step 18 local SLT Flask site.

From SLT_Payment_Project directory run:
    python 19_install_consent_access.py

Merges a scope-limited, expiring LOCAL simulated disclosure preview.
Keeps the existing customer pages unchanged; these are separately authorized by Step 18.
Does not send SMS, establish real customer consent, or enable third-party release.
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
APP = ROOT / "web_app" / "app.py"
AUTH = ROOT / "web_app" / "staff_auth.py"
CONSENT = ROOT / "web_app" / "consent_demo.py"
CONSENT_PAGE = ROOT / "web_app" / "templates" / "consent_demo.html"
ACCESS = ROOT / "web_app" / "consent_access.py"
ACCESS_PAGE = ROOT / "web_app" / "templates" / "consent_disclosure.html"
REQUIRED = (APP, AUTH, CONSENT, CONSENT_PAGE, ACCESS, ACCESS_PAGE)
MARKER = "# >>> STEP 19 CONSENT ACCESS >>>"


def patch_once(src: str, old: str, new: str, file: str) -> str:
    count = src.count(old)
    if count != 1:
        raise RuntimeError(f"Unexpected {file} structure ({count} matches): cannot safely apply Step 19.")
    return src.replace(old, new, 1)


def main():
    missing = [str(p.relative_to(ROOT)) for p in REQUIRED if not p.is_file()]
    if missing:
        raise SystemExit("Missing Step 17/18/19 files:\n- " + "\n- ".join(missing))

    app = APP.read_text(encoding="utf-8")
    auth = AUTH.read_text(encoding="utf-8")
    consent = CONSENT.read_text(encoding="utf-8")
    page = CONSENT_PAGE.read_text(encoding="utf-8")

    if MARKER in app:
        print("Step 19 is already installed. No files changed.")
        return
    if "# >>> STEP 18 STAFF AUTH >>>" not in app or "app.register_blueprint(consent_bp)" not in app:
        raise SystemExit("Install Steps 17 and 18 first. No files changed.")

    app_new = patch_once(app, "app.register_blueprint(consent_bp)",
        "app.register_blueprint(consent_bp)\n" + MARKER + "\n"
        "from consent_access import bp as _step19_access_bp\n"
        "app.extensions['slt_step19_data_context'] = lambda: context()\n"
        "app.register_blueprint(_step19_access_bp)\n"
        "# <<< STEP 19 CONSENT ACCESS <<<", "app.py")

    auth_new = patch_once(auth,
        'or (endpoint is not None and endpoint.startswith("consent_demo."))',
        'or (endpoint is not None and (endpoint.startswith("consent_demo.") or endpoint.startswith("consent_access.")))',
        "staff_auth.py")

    consent_new = patch_once(consent,
        "from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for",
        "from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for\n"
        "from staff_auth import current_staff as _step19_current_staff",
        "consent_demo.py")

    consent_new = patch_once(consent_new,
        "    if not secrets.compare_digest(supplied, session.get('consent_csrf', '')):",
        "    if not supplied or not session.get('consent_csrf') or not secrets.compare_digest(supplied, session['consent_csrf']):",
        "consent_demo.py")

    consent_new = patch_once(consent_new,
        "FROM consent_requests ORDER BY created_ts DESC LIMIT 25''').fetchall()]",
        "FROM consent_requests WHERE requester=? ORDER BY created_ts DESC LIMIT 25''',\n"
        "                    (_step19_current_staff()['username'],)).fetchall()]",
        "consent_demo.py")

    consent_new = patch_once(consent_new,
        "    requester = request.form.get('requester', '').strip()[:70]",
        "    # Requester is bound to the authenticated staff session, not a form field.\n"
        "    staff = _step19_current_staff()\n"
        "    requester = staff['username'] if staff else ''",
        "consent_demo.py")

    consent_new = patch_once(consent_new,
        "    if row is None:\n        abort(404)\n    detail = dict(row)",
        "    if row is None:\n        abort(404)\n"
        "    if row['requester'] != _step19_current_staff()['username']:\n        abort(403)\n"
        "    detail = dict(row)",
        "consent_demo.py")

    consent_new = patch_once(consent_new,
        "        if row is None:\n            abort(404)\n        if display_status(row, now)",
        "        if row is None:\n            abort(404)\n"
        "        if row['requester'] != _step19_current_staff()['username']:\n            abort(403)\n"
        "        if display_status(row, now)",
        "consent_demo.py")

    # Explicitly clarify what simulated approval does and does not authorize.
    page_new = patch_once(page,
        "Practice requesting customer approval before a proposed information disclosure.",
        "Practice scoped information requests with simulated decisions and dummy data.",
        "consent_demo.html")
    page_new = patch_once(page_new,
        "This page does not send SMS, authenticate customers or staff, or grant access to data. Do not use it with real customer records.",
        "This page uses signed-in STAFF test accounts and shows the test code to the requester. "
        "Approval is only simulated; it can unlock a limited dummy-data preview. "
        "It does not contact or verify a real customer or authorize real disclosure.",
        "consent_demo.html")
    page_new = patch_once(page_new,
        "  <p style=\"margin-top:20px\"><a href=\"{{ url_for('consent_demo.index') }}\">← Back to requests</a></p>",
        "  {% if detail.status == 'Approved' and detail.expires_ts >= now and staff_identity and detail.requester == staff_identity.username %}\n"
        "  <p style=\"margin-top:20px\"><a class=\"btn btn-primary\" href=\"{{ url_for('consent_access.disclosure', request_id=detail.request_id) }}\">View scoped dummy-data preview →</a></p>\n"
        "  {% elif detail.status == 'Approved' and detail.expires_ts < now %}\n"
        "  <p class=\"notice\">The approved demo request has expired. Create a new request to test access again.</p>\n"
        "  {% endif %}\n"
        "  <p style=\"margin-top:20px\"><a href=\"{{ url_for('consent_demo.index') }}\">← Back to requests</a></p>",
        "consent_demo.html")
    page_new = patch_once(page_new,
        '<label>Requester\'s name (demo)<input name="requester" placeholder="Authorized staff (demo)" required maxlength="70" style="display:block;width:100%;padding:12px"></label>',
        '<p><strong>Requester (signed-in staff):</strong> {{ staff_identity.username }}</p>',
        "consent_demo.html")
    page_new = patch_once(page_new,
        "Approval does not unlock or expose billing, payment or identity data.",
        "Approved test requests permit only a scoped local dummy-data preview until expiry. "
        "Original internal staff pages remain separately role-protected; this is not real customer consent.",
        "consent_demo.html")

    changed = [(APP, app_new), (AUTH, auth_new), (CONSENT, consent_new), (CONSENT_PAGE, page_new)]
    for path, _ in changed:
        shutil.copy2(path, path.with_name(path.name + ".step19.bak"))
    for path, content in changed:
        path.write_text(content, encoding="utf-8")
    print("Step 19 installed: scope-limited, staff-bound, expiring local disclosure preview.")
    print("Updated original files backed up with .step19.bak suffix.")
    print("Restart: python web_app\\app.py")
    print("Open: http://127.0.0.1:5000/consent-demo")
    print("Use a NEW request made AFTER Step 19 installation.")


if __name__ == "__main__":
    main()
