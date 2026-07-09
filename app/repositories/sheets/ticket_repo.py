"""Google Sheets implementations of TicketRepository and MachineRepository."""

import re
import time
from typing import Dict, List, Optional

from app.repositories.base import (
    MACHINES_HEADER,
    TICKETS_HEADER,
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
