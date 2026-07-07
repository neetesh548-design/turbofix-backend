import secrets
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import gspread
from google.oauth2.service_account import Credentials

from app import config
from app.config import GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_SHEET_ID

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Harden phase: Machines rarely change compared to message volume, so cache the last
# read rather than hitting the Sheets API on every incoming message.
_machines_cache: Optional[Dict[str, dict]] = None
_machines_cache_at: float = 0.0

_TICKETS_HEADER = ["ticket_id", "machine_id", "company_code", "machine_name", "reported_at",
                    "reporter_phone", "description", "ai_summary", "urgency", "status",
                    "closed_at", "hours_to_fix", "voice_note_media_id"]


def _client() -> gspread.Client:
    creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_SCOPES)
    return gspread.authorize(creds)


def _spreadsheet():
    return _client().open_by_key(GOOGLE_SHEET_ID)


def load_machines() -> Dict[str, dict]:
    """Cached in-process for MACHINES_CACHE_TTL_SECONDS - a change to the Machines
    sheet can take up to that long to take effect, in exchange for not hitting the
    Sheets API on every incoming message."""
    global _machines_cache, _machines_cache_at

    now = time.time()
    if _machines_cache is not None and now - _machines_cache_at < config.MACHINES_CACHE_TTL_SECONDS:
        return _machines_cache

    ws = _spreadsheet().worksheet("Machines")
    machines = {}
    for record in ws.get_all_records():
        machine_id = str(record.get("machine_id", "")).strip().upper()
        if not machine_id:
            continue
        informed = [
            record.get("informed_phone_1"), record.get("informed_phone_2"), record.get("informed_phone_3"),
        ]
        machines[machine_id] = {
            "company_code": record.get("company_code"),
            "machine_name": record.get("machine_name"),
            "location": record.get("location"),
            "assigned_technician_phone": record.get("assigned_technician_phone"),
            "informed_phones": [p for p in informed if p],
        }

    _machines_cache = machines
    _machines_cache_at = now
    return machines


def get_machine(machine_id: str) -> Optional[dict]:
    return load_machines().get(machine_id.upper())


def append_ticket(row: dict) -> None:
    ws = _spreadsheet().worksheet("Tickets")
    ws.append_row([row.get(col, "") for col in _TICKETS_HEADER], value_input_option="USER_ENTERED")


def attach_voice_note(ticket_id: str, media_id: str) -> bool:
    ws = _spreadsheet().worksheet("Tickets")
    cell = ws.find(ticket_id, in_column=1)
    if cell is None:
        return False
    media_col = _TICKETS_HEADER.index("voice_note_media_id") + 1
    ws.update_cell(cell.row, media_col, media_id)
    return True


def get_ticket(ticket_id: str) -> Optional[dict]:
    ws = _spreadsheet().worksheet("Tickets")
    cell = ws.find(ticket_id, in_column=1)
    if cell is None:
        return None
    row = ws.row_values(cell.row)
    row += [""] * (len(_TICKETS_HEADER) - len(row))
    return dict(zip(_TICKETS_HEADER, row))


def update_ai_fields(ticket_id: str, ai_summary: str, urgency: str, description: Optional[str] = None) -> bool:
    ws = _spreadsheet().worksheet("Tickets")
    cell = ws.find(ticket_id, in_column=1)
    if cell is None:
        return False
    ws.update_cell(cell.row, _TICKETS_HEADER.index("ai_summary") + 1, ai_summary)
    ws.update_cell(cell.row, _TICKETS_HEADER.index("urgency") + 1, urgency)
    if description is not None:
        ws.update_cell(cell.row, _TICKETS_HEADER.index("description") + 1, description)
    return True


def next_ticket_id() -> str:
    return f"T{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"
