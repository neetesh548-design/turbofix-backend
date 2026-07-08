"""Google Sheets-backed equivalent of users_store_local.py - same five functions,
same Users/Companies tab schemas, used when TICKET_STORE=sheets. See
app/store_sheets.py for the same local/sheets split applied to machines/tickets."""

import secrets
from datetime import datetime, timezone
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from app.config import GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_SHEET_ID

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_USERS_HEADER = ["user_id", "company_code", "name", "phone", "email", "role", "password_hash", "created_at"]


def _client() -> gspread.Client:
    creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_SCOPES)
    return gspread.authorize(creds)


def _spreadsheet():
    return _client().open_by_key(GOOGLE_SHEET_ID)


def next_user_id(company_code: str) -> str:
    return f"U-{company_code}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


def _normalize(value) -> str:
    # get_all_records() returns numeric-looking cells (e.g. phone numbers) as ints,
    # so coerce to str before normalizing.
    return str(value).strip().lower() if value is not None else ""


def get_user_by_identifier(identifier: str) -> Optional[dict]:
    """Looks a user up by phone or email (case-insensitive, whitespace-trimmed)."""
    ws = _spreadsheet().worksheet("Users")
    target = _normalize(identifier)
    for record in ws.get_all_records():
        if _normalize(record.get("phone")) == target or _normalize(record.get("email")) == target:
            return record
    return None


def get_user_by_id(user_id: str) -> Optional[dict]:
    ws = _spreadsheet().worksheet("Users")
    for record in ws.get_all_records():
        if record.get("user_id") == user_id:
            return record
    return None


def get_company(company_code: str) -> Optional[dict]:
    """Looks up a row in the Companies tab by company_code - used by self-service
    signup to verify a company's admin_contact_phone before creating a supervisor
    account under it."""
    ws = _spreadsheet().worksheet("Companies")
    target = _normalize(company_code)
    for record in ws.get_all_records():
        if _normalize(record.get("company_code")) == target:
            return record
    return None


def add_user(row: dict) -> None:
    """row keys: user_id, company_code, name, phone, email, role, password_hash,
    created_at."""
    ws = _spreadsheet().worksheet("Users")
    # RAW so phone numbers stay text instead of being coerced to numbers.
    ws.append_row([row.get(col, "") for col in _USERS_HEADER], value_input_option="RAW")
