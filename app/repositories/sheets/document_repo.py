"""Google Sheets implementation of DocumentRepository.

Previously documents_store.py only had a local (openpyxl) implementation.
This is the NEW Sheets-backed version required by the SOLID architecture.
"""

from typing import List, Optional

from app.repositories.base import (
    DOCUMENTS_HEADER,
    DocumentRepository,
    new_document_id,
)
from app.repositories.sheets.client import get_spreadsheet


class SheetsDocumentRepository(DocumentRepository):
    """Reads/writes document metadata in the Documents worksheet of a Google Sheet."""

    def __init__(self, service_account_file: str, sheet_id: str):
        self._sa_file = service_account_file
        self._sheet_id = sheet_id

    def _ws(self):
        return get_spreadsheet(self._sa_file, self._sheet_id).worksheet("Documents")

    def next_document_id(self) -> str:
        return new_document_id()

    def list(self, company_code: str, machine_id: Optional[str] = None) -> List[dict]:
        all_rows = self._ws().get_all_records()
        results = []
        for record in all_rows:
            if record.get("company_code") != company_code:
                continue
            if machine_id is not None and record.get("machine_id") != machine_id:
                continue
            results.append(record)
        return results

    def get(self, document_id: str) -> Optional[dict]:
        ws = self._ws()
        cell = ws.find(document_id, in_column=1)
        if cell is None:
            return None
        row = ws.row_values(cell.row)
        row += [""] * (len(DOCUMENTS_HEADER) - len(row))
        return dict(zip(DOCUMENTS_HEADER, row))

    def add(self, row: dict) -> None:
        self._ws().append_row(
            [row.get(col, "") for col in DOCUMENTS_HEADER], value_input_option="RAW"
        )

    def delete(self, document_id: str) -> bool:
        ws = self._ws()
        cell = ws.find(document_id, in_column=1)
        if cell is None:
            return False
        ws.delete_rows(cell.row)
        return True
