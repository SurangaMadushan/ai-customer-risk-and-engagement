"""Step 20 — read-only integration smoke tests for SLT Customer Intelligence.

Run from SLT_Payment_Project (the existing local dummy-data project):
    python 20_final_integration_test.py

Uses Flask's in-process test client. Does not send SMS, change customer data,
verify real customer consent, or test an external deployment. Uses simulated
staff sessions only for permission checks; your actual staff login must also
be tested manually. Step 19 disclosure records are mocked, so no consent
records or audit logs are created by these automated tests.
"""
from __future__ import annotations

import importlib.util
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "step20_final_validation_report.txt"
WEB = ROOT / "web_app"
SCORES = OUT.parent / "customer_creditworthiness.csv"
RECS = OUT.parent / "collection_recommendations.csv"
WORKBOOK = ROOT / "data" / "SLT_Payment_Behaviour_Dummy_Data.xlsx"
RESULTS: list[tuple[str, str, str]] = []


def test(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def skip(name: str, reason: str) -> None:
    RESULTS.append(("SKIP", name, reason))
    print(f"[SKIP] {name} — {reason}")


def normalize(series):
    return series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def data_checks() -> tuple[str, str] | None:
    """Return one reliable (account number, customer ID) fixture."""
    needed = [WORKBOOK, SCORES, RECS]
    missing = [str(p.relative_to(ROOT)) for p in needed if not p.is_file()]
    test("Dummy workbook and Step 14/15 CSVs present", not missing, ", ".join(missing))
    if missing:
        return None
    try:
        import pandas as pd
        source = pd.read_excel(WORKBOOK, sheet_name="Customer_Master", engine="openpyxl")
        summary = pd.read_excel(WORKBOOK, sheet_name="Behaviour_Summary", engine="openpyxl")
        score = pd.read_csv(SCORES, dtype={"account_no": "string", "customer_id": "string"})
        rec = pd.read_csv(RECS, dtype={"account_no": "string", "customer_id": "string"})
        for frame in (source, summary, score, rec):
            frame["account_no"] = normalize(frame["account_no"])
            frame["customer_id"] = normalize(frame["customer_id"])
    except Exception as exc:
        test("Read workbook and analysis outputs", False, f"{type(exc).__name__}: {exc}")
        return None
    test("Read workbook and analysis outputs", True)

    counts = (len(source), len(summary), len(score), len(rec))
    test("All four customer-level sources have 200 rows", counts == (200, 200, 200, 200), str(counts))
    unique = all(f["account_no"].notna().all() and f["account_no"].is_unique
                 for f in (source, summary, score, rec))
    test("Customer-level account numbers are unique and non-empty", unique)
    pairs = [set(zip(f["account_no"], f["customer_id"])) for f in (source, summary, score, rec)]
    test("Customer/account ID pairs match across sources", all(p == pairs[0] for p in pairs[1:]))

    numeric = pd.to_numeric(score["payment_reliability_score"], errors="coerce")
    test("All reliability scores are in range 0–100", numeric.notna().all() and numeric.between(0, 100).all())
    point_columns = ["points_settlement", "points_consistency", "points_delay",
                     "points_current_arrears", "points_balance_exposure", "points_collection_history"]
    if all(col in score.columns for col in point_columns):
        parts = score[point_columns].apply(pd.to_numeric, errors="coerce")
        valid = parts.notna().all().all() and (parts.sum(axis=1) - numeric).abs().le(0.11).all()
        test("Score matches six components (within 0.11 rounding)", valid)
    else:
        test("Six creditworthiness score components available", False,
             "Missing: " + ", ".join(c for c in point_columns if c not in score.columns))

    allowed = {"No follow-up", "Normal follow-up", "Priority follow-up"}
    test("Follow-up actions have permitted values",
         rec["recommended_action"].notna().all()
         and set(rec["recommended_action"].dropna()) <= allowed,
         str(rec["recommended_action"].value_counts().to_dict()))
    test("Every follow-up has a written explanation",
         rec["recommendation_reason"].fillna("").astype(str).str.strip().ne("").all())

    def overdue(frame):
        return (pd.to_numeric(frame["unpaid_past_due_bills"], errors="coerce").gt(0)
                & pd.to_numeric(frame["current_overdue_days"], errors="coerce").gt(0)
                & pd.to_numeric(frame["current_outstanding_LKR"], errors="coerce").gt(0))

    src_overdue = overdue(summary)
    sc_overdue = overdue(score)
    expected = set(summary.loc[src_overdue, "account_no"])
    actual = set(score.loc[sc_overdue, "account_no"])
    test("Overdue accounts agree with dummy workbook", expected == actual,
         f"source={len(expected)}, output={len(actual)}")
    source_amt = pd.to_numeric(summary.loc[src_overdue, "current_outstanding_LKR"], errors="coerce").sum()
    score_amt = pd.to_numeric(score.loc[sc_overdue, "current_outstanding_LKR"], errors="coerce").sum()
    test("Overdue balance agrees within 1 cent", math.isclose(source_amt, score_amt, abs_tol=0.01),
         f"LKR {score_amt:,.2f}")
    actions = rec.set_index("account_no")["recommended_action"]
    bad = [account for account in actual if actions.get(account) == "No follow-up"]
    test("No currently overdue account marked No follow-up", not bad,
         f"violations={len(bad)}")

    preferred = source.loc[source["account_no"].eq("401934")]
    if not preferred.empty:
        row = preferred.iloc[0]
    else:
        row = source.iloc[0]
    return str(row["account_no"]), str(row["customer_id"])


def flask_checks(fixture: tuple[str, str]) -> None:
    needed = [WEB / "app.py", WEB / "staff_auth.py", WEB / "consent_demo.py",
              WEB / "consent_access.py", WEB / "templates" / "consent_disclosure.html"]
    missing = [str(p.relative_to(ROOT)) for p in needed if not p.is_file()]
    test("Flask Steps 17–19 module files present", not missing, ", ".join(missing))
    if missing:
        return
    try:
        # web_app is not a package in the original project; support its direct imports.
        sys.path.insert(0, str(WEB))
        spec = importlib.util.spec_from_file_location("step20_local_site", WEB / "app.py")
        if not spec or not spec.loader:
            raise RuntimeError("Could not load web_app/app.py")
        module = importlib.util.module_from_spec(spec)
        # Register the module before executing it so Flask can resolve its
        # root_path as web_app/ and find web_app/templates/*.html.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        import staff_auth
        import consent_access
        app = module.app
        test("Flask application imports with authentication modules", True)
    except Exception as exc:
        test("Flask application imports with authentication modules", False,
             f"{type(exc).__name__}: {exc}")
        return

    account_no, customer_id = fixture
    anon = app.test_client()
    for url in ("/", f"/customers/{account_no}", "/consent-demo"):
        resp = anon.get(url)
        test(f"Anonymous request redirected to login: {url}",
             resp.status_code in (301, 302, 303, 307, 308)
             and "/staff-login" in resp.headers.get("Location", ""),
             f"HTTP {resp.status_code}")
    login = anon.get("/staff-login")
    test("Staff login page accessible", login.status_code == 200, f"HTTP {login.status_code}")

    # In-process role simulations only. These do not authenticate a user or
    # grant access in a running web server; no credentials are read or modified.
    identities = {
        "step20_admin": "admin",
        "step20_analyst": "analyst",
        "step20_viewer": "viewer",
    }

    def mock_load_user(username):
        role = identities.get(username)
        return {"username": username, "role": role, "is_active": 1} if role else None

    def for_role(username):
        client = app.test_client()
        with client.session_transaction() as session:
            session["staff_username"] = username
        return client

    with patch.object(staff_auth, "_load_user", side_effect=mock_load_user):
        for username in ("step20_admin", "step20_analyst"):
            client = for_role(username)
            role = identities[username]
            for url in ("/", f"/customers?q={customer_id}", f"/customers/{account_no}",
                        "/creditworthiness", "/collections", "/consent-demo"):
                resp = client.get(url)
                test(f"{role.title()} can open {url}", resp.status_code == 200,
                     f"HTTP {resp.status_code}")
            response = client.get(f"/customers/{account_no}")
            test(f"{role.title()} profile identifies selected customer",
                 response.status_code == 200 and customer_id in response.get_data(as_text=True))

        viewer = for_role("step20_viewer")
        resp = viewer.get("/")
        test("Viewer can see overview", resp.status_code == 200, f"HTTP {resp.status_code}")
        test("Viewer overview hides individual selected account",
             resp.status_code == 200 and account_no not in resp.get_data(as_text=True)
             and customer_id not in resp.get_data(as_text=True))
        for url in (f"/customers/{account_no}", "/customers", "/creditworthiness",
                    "/collections", "/consent-demo"):
            resp = viewer.get(url)
            test(f"Viewer denied {url}", resp.status_code == 403,
                 f"HTTP {resp.status_code}")
        resp = for_role("step20_admin").get("/")
        test("Internal pages disallow caching", "no-store" in resp.headers.get("Cache-Control", ""))
        test("Clickjacking and MIME-type response headers present",
             resp.headers.get("X-Frame-Options") == "DENY"
             and resp.headers.get("X-Content-Type-Options") == "nosniff")

        # Test Step 19 authorization cases against synthetic records without
        # creating consent requests or writing an audit log.
        request_id = "f" * 32
        template = {
            "request_id": request_id, "account_no": account_no,
            "requester": "step20_admin", "purpose": "Automated demo integration test",
            "data_scope": "Account summary", "status": "Approved",
            "decided_ts": int(time.time()), "expires_ts": int(time.time()) + 240,
        }
        audit_events = []
        def mock_audit(*args, **kwargs):
            audit_events.append(args)
        with patch.object(consent_access, "_get_record", return_value=template.copy()) as get_record, \
             patch.object(consent_access, "_audit", side_effect=mock_audit):
            own = for_role("step20_admin").get(f"/consent-demo/disclosure/{request_id}")
            test("Approved request displays scoped account summary for owner",
                 own.status_code == 200 and account_no in own.get_data(as_text=True)
                 and customer_id in own.get_data(as_text=True), f"HTTP {own.status_code}")
            test("Account-summary disclosure excludes billing/payment detail labels",
                 own.status_code == 200
                 and "Number of recorded invoices" not in own.get_data(as_text=True)
                 and "Unpaid past-due bills" not in own.get_data(as_text=True))
            other = for_role("step20_analyst").get(f"/consent-demo/disclosure/{request_id}")
            test("Another staff member denied disclosure", other.status_code == 403,
                 f"HTTP {other.status_code}")

            rejected = dict(template, status="Rejected")
            get_record.return_value = rejected
            resp = for_role("step20_admin").get(f"/consent-demo/disclosure/{request_id}")
            test("Rejected disclosure denied", resp.status_code == 403,
                 f"HTTP {resp.status_code}")
            expired = dict(template, expires_ts=int(time.time()) - 10)
            get_record.return_value = expired
            resp = for_role("step20_admin").get(f"/consent-demo/disclosure/{request_id}")
            test("Expired disclosure denied", resp.status_code == 403,
                 f"HTTP {resp.status_code}")
            invalid = dict(template, data_scope="Full customer database")
            get_record.return_value = invalid
            resp = for_role("step20_admin").get(f"/consent-demo/disclosure/{request_id}")
            test("Unsupported disclosure scope denied", resp.status_code == 403,
                 f"HTTP {resp.status_code}")
            test("Simulated disclosure decisions exercised audit hook", len(audit_events) >= 4,
                 "audit database was NOT modified")


def main() -> int:
    print("\nSLT STEP 20 — FINAL LOCAL INTEGRATION SMOKE TEST")
    print("Dummy data only. No SMS, no real customer consent, no deployment.\n")
    fixture = data_checks()
    if fixture is not None:
        flask_checks(fixture)
    else:
        skip("Flask tests", "Cannot choose customer fixture without dummy workbook and outputs")

    ignore = ROOT / ".gitignore"
    if ignore.is_file():
        content = ignore.read_text(encoding="utf-8", errors="replace")
        targets = ("staff_auth.sqlite3", "consent_demo.sqlite3", "staff_auth_secret")
        test("Local credentials/consent storage covered in root .gitignore",
             all(t in content for t in targets))
    else:
        test("Root .gitignore exists", False, "Do not commit local test auth databases/secrets")

    failed = sum(status == "FAIL" for status, _, _ in RESULTS)
    passed = sum(status == "PASS" for status, _, _ in RESULTS)
    skipped = sum(status == "SKIP" for status, _, _ in RESULTS)
    lines = [
        "SLT Customer Intelligence — Step 20 Local Integration Smoke Test",
        "Generated: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Environment: Local, 200 synthetic/dummy accounts",
        "Purpose: Regression/integration smoke test; NOT production security certification.",
        f"RESULT: {'PASS' if failed == 0 else 'NEEDS ATTENTION'}",
        f"Checks: {passed} passed, {failed} failed, {skipped} skipped",
        "",
        "IMPORTANT LIMITATIONS:",
        "- Staff role checks use Flask test-client sessions, not password-based sign-in.",
        "- Disclosure authorization uses mocked demo records; the test does not send OTP/SMS.",
        "- Real consent, external deployment, hardening and manual usability need separate testing.",
        "- Existing internal customer pages are staff-role protected, not individually consent-gated.",
        "",
    ]
    lines.extend(f"{status}: {name}" + (f" | {detail}" if detail else "") for status, name, detail in RESULTS)
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nReport saved: {OUT.relative_to(ROOT)}")
    except OSError as exc:
        print(f"\nCould not write report: {exc}")
    print(f"RESULT: {passed} PASS, {failed} FAIL, {skipped} SKIP")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
