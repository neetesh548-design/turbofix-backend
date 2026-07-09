"""Google Sheets implementation of UserRepository."""

from typing import List, Optional

from app.repositories.base import (
    COMPANIES_HEADER,
    USERS_HEADER,
    UserRepository,
    new_user_id,
)
from app.repositories.sheets.client import get_spreadsheet


def _normalize(value) -> str:
    # get_all_records() may return numeric-looking cells (phone numbers) as ints.
    return str(value).strip().lower() if value is not None else ""


class SheetsUserRepository(UserRepository):
    """Reads/writes Users and Companies worksheets in a Google Sheet."""

    def __init__(self, service_account_file: str, sheet_id: str):
        self._sa_file = service_account_file
        self._sheet_id = sheet_id

    def _ss(self):
        return get_spreadsheet(self._sa_file, self._sheet_id)

    def next_user_id(self, company_code: str) -> str:
        return new_user_id(company_code)

    def get_by_identifier(self, identifier: str) -> Optional[dict]:
        ws = self._ss().worksheet("Users")
        target = _normalize(identifier)
        for record in ws.get_all_records():
            if _normalize(record.get("phone")) == target or _normalize(record.get("email")) == target:
                return record
        return None

    def get_by_id(self, user_id: str) -> Optional[dict]:
        ws = self._ss().worksheet("Users")
        for record in ws.get_all_records():
            if record.get("user_id") == user_id:
                return record
        return None

    def add(self, row: dict) -> None:
        ws = self._ss().worksheet("Users")
        ws.append_row([row.get(col, "") for col in USERS_HEADER], value_input_option="RAW")

    def update_password(self, user_id: str, new_password_hash: str) -> bool:
        ws = self._ss().worksheet("Users")
        cell = ws.find(user_id, in_column=1)
        if cell is None:
            return False
        hash_col = USERS_HEADER.index("password_hash") + 1
        ws.update_cell(cell.row, hash_col, new_password_hash)
        return True

    def get_company(self, company_code: str) -> Optional[dict]:
        ws = self._ss().worksheet("Companies")
        target = _normalize(company_code)
        for record in ws.get_all_records():
            if _normalize(record.get("company_code")) == target:
                return record
        return None

    def list_companies(self) -> List[dict]:
        ws = self._ss().worksheet("Companies")
        return list(ws.get_all_records())

    def update_company(self, company_code: str, fields: dict) -> bool:
        ws = self._ss().worksheet("Companies")
        header = ws.row_values(1)
        for col in fields:
            if col not in header:
                header.append(col)
                ws.update_cell(1, len(header), col)
        cell = ws.find(company_code, in_column=1)
        if cell is None:
            return False
        for key, value in fields.items():
            if key in header:
                ws.update_cell(cell.row, header.index(key) + 1, value)
        return True
