"""Admin CLI for creating a Document Vault login (owner/supervisor/maintenance_head).

Creating a login is a deliberate admin action (company onboarding), not a self-serve
signup - there is no API endpoint for it, only this script. Run from backend/:

    .venv/bin/python scripts/create_user.py \\
        --company-code ACME3 --name "Rakesh Shah" --phone +919820012345 \\
        --email rakesh@acmeforge.example --role owner
"""

import argparse
import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import Role, hash_password
from app.users_store import add_user, next_user_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-code", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--phone", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--role", required=True, choices=[r.value for r in Role])
    args = parser.parse_args()

    if not args.phone and not args.email:
        parser.error("at least one of --phone or --email is required (used for login)")

    password = getpass.getpass("Set a password for this user: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        parser.error("passwords did not match")

    user_id = next_user_id(args.company_code)
    add_user({
        "user_id": user_id,
        "company_code": args.company_code,
        "name": args.name,
        "phone": args.phone,
        "email": args.email,
        "role": args.role,
        "password_hash": hash_password(password),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })
    print(f"Created {args.role} user {user_id} ({args.name}, {args.company_code})")


if __name__ == "__main__":
    main()
