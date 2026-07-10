"""Google Sheets implementation of CustomKpiRepository."""

from typing import List, Optional

from app.repositories.base import (
    CUSTOM_KPIS_HEADER,
    KPI_DATA_HEADER,
    CustomKpiRepository,
    new_kpi_entry_id,
    new_kpi_id,
)
from app.repositories.sheets.client import get_spreadsheet


class SheetsCustomKpiRepository(CustomKpiRepository):
    def __init__(self, service_account_file: str, sheet_id: str):
        self._sa_file = service_account_file
        self._sheet_id = sheet_id

    def _ws_kpis(self):
        ss = get_spreadsheet(self._sa_file, self._sheet_id)
        try:
            return ss.worksheet("CustomKPIs")
        except Exception:
            ws = ss.add_worksheet(title="CustomKPIs", rows=100, cols=len(CUSTOM_KPIS_HEADER))
            ws.append_row(CUSTOM_KPIS_HEADER, value_input_option="RAW")
            return ws

    def _ws_data(self):
        ss = get_spreadsheet(self._sa_file, self._sheet_id)
        try:
            return ss.worksheet("KPIData")
        except Exception:
            ws = ss.add_worksheet(title="KPIData", rows=1000, cols=len(KPI_DATA_HEADER))
            ws.append_row(KPI_DATA_HEADER, value_input_option="RAW")
            return ws

    def list_kpis(self, company_code: str) -> List[dict]:
        rows = self._ws_kpis().get_all_records()
        return [r for r in rows if r.get("company_code") == company_code]

    def get_kpi(self, kpi_id: str) -> Optional[dict]:
        ws = self._ws_kpis()
        cell = ws.find(kpi_id, in_column=1)
        if cell is None:
            return None
        row = ws.row_values(cell.row)
        row += [""] * (len(CUSTOM_KPIS_HEADER) - len(row))
        return dict(zip(CUSTOM_KPIS_HEADER, row))

    def add_kpi(self, row: dict) -> None:
        if not row.get("kpi_id"):
            row["kpi_id"] = new_kpi_id()
        self._ws_kpis().append_row(
            [row.get(col, "") for col in CUSTOM_KPIS_HEADER], value_input_option="RAW"
        )

    def update_kpi(self, kpi_id: str, updates: dict) -> bool:
        ws = self._ws_kpis()
        cell = ws.find(kpi_id, in_column=1)
        if cell is None:
            return False
        for key, val in updates.items():
            if key in CUSTOM_KPIS_HEADER:
                col = CUSTOM_KPIS_HEADER.index(key) + 1
                ws.update_cell(cell.row, col, val)
        return True

    def delete_kpi(self, kpi_id: str) -> bool:
        ws = self._ws_kpis()
        cell = ws.find(kpi_id, in_column=1)
        if cell is None:
            return False
        ws.delete_rows(cell.row)
        return True

    def list_data(self, company_code: str, kpi_id: Optional[str] = None, limit: int = 30) -> List[dict]:
        rows = self._ws_data().get_all_records()
        filtered = [r for r in rows if r.get("company_code") == company_code]
        if kpi_id:
            filtered = [r for r in filtered if r.get("kpi_id") == kpi_id]
        filtered.sort(key=lambda x: str(x.get("recorded_at", "")), reverse=True)
        return filtered[:limit]

    def add_data(self, row: dict) -> None:
        if not row.get("entry_id"):
            row["entry_id"] = new_kpi_entry_id()
        self._ws_data().append_row(
            [row.get(col, "") for col in KPI_DATA_HEADER], value_input_option="RAW"
        )
