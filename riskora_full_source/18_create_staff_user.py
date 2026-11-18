"""Create a local Step 18 TEST staff account; run from SLT_Payment_Project root.

Examples:
  python 18_create_staff_user.py --username admin1 --role admin
  python 18_create_staff_user.py --username analyst1 --role analyst
  python 18_create_staff_user.py --username viewer1 --role viewer
"""
import argparse
from getpass import getpass

from web_app.staff_auth import add_staff_user, ROLES


def main():
    parser = argparse.ArgumentParser(description="Create local dummy-data website staff login")
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", required=True, choices=ROLES)
    args = parser.parse_args()

    print("Local test user only. Do NOT reuse a real company password.")
    password = getpass("Choose a NEW test password (12+ characters): ")
    repeated = getpass("Confirm test password: ")
    if password != repeated:
        raise SystemExit("Passwords did not match. No user was created.")
    try:
        add_staff_user(args.username, password, args.role)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Created test account '{args.username.strip().lower()}' with role '{args.role}'.")
    print("Password is stored as a hash. Keep the password private.")


if __name__ == "__main__":
    main()
