"""Google Sheets implementation of PartsRepository.

Previously parts_store.py only had a local (openpyxl) implementation.
This is the NEW Sheets-backed version required by the SOLID architecture.
"""

from typing import List, Optional

from app.repositories.base import (
    CONSUMABLES_HEADER,
    SPARE_PARTS_HEADER,
    PartsRepository,
    new_item_id,
)
from app.repositories.sheets.client import get_spreadsheet

_SHEETS = {
    "spare_parts": ("SpareParts", SPARE_PARTS_HEADER),
    "consumables": ("Consumables", CONSUMABLES_HEADER),
}


class SheetsPartsRepository(PartsRepository):
    """Reads/writes spare parts and consumables worksheets in a Google Sheet."""

    def __init__(self, service_account_file: str, sheet_id: str):
        self._sa_file = service_account_file
        self._sheet_id = sheet_id

    def _ws(self, kind: str):
        sheet_name, _ = _SHEETS[kind]
        return get_spreadsheet(self._sa_file, self._sheet_id).worksheet(sheet_name)

    def next_item_id(self, kind: str) -> str:
        return new_item_id(kind)

    def list_items(
        self, kind: str, company_code: str, machine_id: Optional[str] = None
    ) -> List[dict]:
        all_rows = self._ws(kind).get_all_records()
        results = []
        for record in all_rows:
            if record.get("company_code") != company_code:
                continue
            if machine_id is not None and record.get("machine_id") != machine_id:
                continue
            results.append(record)
        return results

    def get_item(self, kind: str, item_id: str) -> Optional[dict]:
        _, header = _SHEETS[kind]
        ws = self._ws(kind)
        cell = ws.find(item_id, in_column=1)
        if cell is None:
            return None
        row = ws.row_values(cell.row)
        row += [""] * (len(header) - len(row))
        return dict(zip(header, row))

    def add_item(self, kind: str, row: dict) -> None:
        _, header = _SHEETS[kind]
        self._ws(kind).append_row(
            [row.get(col, "") for col in header], value_input_option="RAW"
        )

    def update_item(self, kind: str, item_id: str, updates: dict) -> bool:
        _, header = _SHEETS[kind]
        ws = self._ws(kind)
        cell = ws.find(item_id, in_column=1)
        if cell is None:
            return False
        for field, value in updates.items():
            if field in header:
                ws.update_cell(cell.row, header.index(field) + 1, value)
        return True

    def delete_item(self, kind: str, item_id: str) -> bool:
        ws = self._ws(kind)
        cell = ws.find(item_id, in_column=1)
        if cell is None:
            return False
        ws.delete_rows(cell.row)
        return True
