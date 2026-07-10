"""Google Sheets implementations of TicketRepository and MachineRepository."""

import re
import time
from typing import Dict, List, Optional

from app.repositories.base import (
    MACHINE_EVENTS_HEADER,
    MACHINES_HEADER,
    TICKETS_HEADER,
    EventRepository,
    MachineRepository,
    TicketRepository,
    new_ticket_id,
)
from app.repositories.sheets.client import get_spreadsheet


class SheetsTicketRepository(TicketRepository):
    """Reads/writes tickets in the Tickets worksheet of a Google Sheet."""

    def __init__(self, service_account_file: str, sheet_id: str):
        self._sa_file = service_account_file
        self._sheet_id = sheet_id

    def _ws(self):
        return get_spreadsheet(self._sa_file, self._sheet_id).worksheet("Tickets")

    def next_ticket_id(self) -> str:
        return new_ticket_id()

    def append(self, row: dict) -> None:
        # RAW so phone numbers stay text instead of being coerced to numbers.
        self._ws().append_row(
            [row.get(col, "") for col in TICKETS_HEADER], value_input_option="RAW"
        )

    def get(self, ticket_id: str) -> Optional[dict]:
        ws = self._ws()
        cell = ws.find(ticket_id, in_column=1)
        if cell is None:
            return None
        row = ws.row_values(cell.row)
        row += [""] * (len(TICKETS_HEADER) - len(row))
        return dict(zip(TICKETS_HEADER, row))

    def attach_voice_note(self, ticket_id: str, media_id: str) -> bool:
        ws = self._ws()
        cell = ws.find(ticket_id, in_column=1)
        if cell is None:
            return False
        media_col = TICKETS_HEADER.index("voice_note_media_id") + 1
        ws.update_cell(cell.row, media_col, media_id)
        return True

    def update_ai_fields(
        self,
        ticket_id: str,
        ai_summary: str,
        urgency: str,
        description: Optional[str] = None,
    ) -> bool:
        ws = self._ws()
        cell = ws.find(ticket_id, in_column=1)
        if cell is None:
            return False
        ws.update_cell(cell.row, TICKETS_HEADER.index("ai_summary") + 1, ai_summary)
        ws.update_cell(cell.row, TICKETS_HEADER.index("urgency") + 1, urgency)
        if description is not None:
            ws.update_cell(cell.row, TICKETS_HEADER.index("description") + 1, description)
        return True

    def get_company_tickets(self, company_code: str) -> List[dict]:
        ws = self._ws()
        all_rows = ws.get_all_records()
        return [r for r in all_rows if r.get("company_code") == company_code]

    def attach_photo(self, ticket_id: str, media_id: str) -> bool:
        ws = self._ws()
        cell = ws.find(ticket_id, in_column=1)
        if cell is None:
            return False
        ws.update_cell(cell.row, TICKETS_HEADER.index("photo_media_id") + 1, media_id)
        return True

    def update_language(self, ticket_id: str, language: str) -> bool:
        ws = self._ws()
        cell = ws.find(ticket_id, in_column=1)
        if cell is None:
            return False
        ws.update_cell(cell.row, TICKETS_HEADER.index("language") + 1, language)
        return True

    def close_ticket(self, ticket_id: str, closed_by: str) -> bool:
        from datetime import datetime, timezone
        ws = self._ws()
        cell = ws.find(ticket_id, in_column=1)
        if cell is None:
            return False
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        ws.update_cell(cell.row, TICKETS_HEADER.index("status") + 1, "Closed")
        ws.update_cell(cell.row, TICKETS_HEADER.index("closed_at") + 1, now)
        ws.update_cell(cell.row, TICKETS_HEADER.index("closed_by") + 1, closed_by)
        return True

    def find_by_id_prefix(self, prefix: str) -> Optional[dict]:
        ws = self._ws()
        all_rows = ws.get_all_values()
        prefix_upper = prefix.upper()
        for row in all_rows[1:]:
            if row and row[0] and row[0].upper().startswith(prefix_upper):
                row += [""] * (len(TICKETS_HEADER) - len(row))
                return dict(zip(TICKETS_HEADER, row))
        return None


class SheetsEventRepository(EventRepository):
    """Reads/writes events in the MachineEvents worksheet of a Google Sheet."""

    def __init__(self, service_account_file: str, sheet_id: str):
        self._sa_file = service_account_file
        self._sheet_id = sheet_id

    def _ws(self):
        sp = get_spreadsheet(self._sa_file, self._sheet_id)
        try:
            return sp.worksheet("MachineEvents")
        except Exception:
            ws = sp.add_worksheet(title="MachineEvents", rows=1000, cols=len(MACHINE_EVENTS_HEADER))
            ws.append_row(MACHINE_EVENTS_HEADER, value_input_option="RAW")
            return ws

    def append(self, row: dict) -> None:
        self._ws().append_row(
            [row.get(col, "") for col in MACHINE_EVENTS_HEADER], value_input_option="RAW"
        )

    def get_machine_events(self, machine_id: str) -> List[dict]:
        all_rows = self._ws().get_all_records()
        return [r for r in all_rows if r.get("machine_id") == machine_id]

    def get_company_events(self, company_code: str) -> List[dict]:
        all_rows = self._ws().get_all_records()
        return [r for r in all_rows if r.get("company_code") == company_code]


class SheetsMachineRepository(MachineRepository):
    """Reads/writes machines in the Machines worksheet of a Google Sheet.

    Maintains an in-process cache keyed by TTL to avoid hitting the Sheets
    API on every incoming message (same logic as the legacy store_sheets.py).
    """

    def __init__(self, service_account_file: str, sheet_id: str, cache_ttl: int = 60):
        self._sa_file = service_account_file
        self._sheet_id = sheet_id
        self._cache_ttl = cache_ttl
        self._cache: Optional[Dict[str, dict]] = None
        self._cache_at: float = 0.0

    def _ws(self):
        return get_spreadsheet(self._sa_file, self._sheet_id).worksheet("Machines")

    def load(self) -> Dict[str, dict]:
        now = time.time()
        if self._cache is not None and now - self._cache_at < self._cache_ttl:
            return self._cache

        machines: Dict[str, dict] = {}
        for record in self._ws().get_all_records():
            machine_id = str(record.get("machine_id", "")).strip().upper()
            if not machine_id:
                continue
            informed = [
                record.get("informed_phone_1"),
                record.get("informed_phone_2"),
                record.get("informed_phone_3"),
            ]
            machines[machine_id] = {
                "company_code": record.get("company_code"),
                "machine_name": record.get("machine_name"),
                "location": record.get("location"),
                "assigned_technician_phone": str(record.get("assigned_technician_phone") or ""),
                "informed_phones": [str(p) for p in informed if p],
                "supervisor_id": str(record.get("supervisor_id") or ""),
                "last_activity_at": str(record.get("last_activity_at") or ""),
            }

        self._cache = machines
        self._cache_at = now
        return machines

    def get(self, machine_id: str) -> Optional[dict]:
        return self.load().get(machine_id.upper())

    def create(self, row: dict) -> None:
        ws = self._ws()
        data_cols = MACHINES_HEADER[:-1]
        ws.append_row([row.get(col, "") for col in data_cols], value_input_option="RAW")
        row_num = len(ws.get_all_values())
        ws.update_cell(
            row_num, len(MACHINES_HEADER),
            f'=COUNTIFS(Tickets!$B:$B,A{row_num},Tickets!$J:$J,"Open")',
        )
        self.invalidate_cache()

    def invalidate_cache(self) -> None:
        self._cache = None
        self._cache_at = 0.0

    def next_machine_code(self, company_code: str) -> str:
        self.invalidate_cache()
        pattern = re.compile(rf"^TF-{re.escape(company_code)}-M(\d+)$", re.IGNORECASE)
        max_n = 0
        for machine_id in self.load():
            m = pattern.match(machine_id)
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"M{max_n + 1:03d}"

    def get_company_machines(self, company_code: str) -> List[dict]:
        ws = self._ws()
        all_rows = ws.get_all_records()
        return [r for r in all_rows if r.get("company_code") == company_code]

    def update_machine(self, machine_id: str, fields: dict) -> bool:
        ws = self._ws()
        header = ws.row_values(1)
        cell = ws.find(machine_id.upper(), in_column=1)
        if cell is None:
            return False
        for key, val in fields.items():
            if key in header:
                ws.update_cell(cell.row, header.index(key) + 1, val)
        self.invalidate_cache()
        return True
