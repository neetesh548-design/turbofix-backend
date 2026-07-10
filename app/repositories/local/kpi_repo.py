"""In-memory implementation of CustomKpiRepository for local dev/testing."""

from typing import List, Optional

from app.repositories.base import CustomKpiRepository, new_kpi_entry_id, new_kpi_id


class LocalCustomKpiRepository(CustomKpiRepository):
    def __init__(self):
        self._kpis: list[dict] = []
        self._data: list[dict] = []

    def list_kpis(self, company_code: str) -> List[dict]:
        return [k for k in self._kpis if k.get("company_code") == company_code]

    def get_kpi(self, kpi_id: str) -> Optional[dict]:
        return next((k for k in self._kpis if k.get("kpi_id") == kpi_id), None)

    def add_kpi(self, row: dict) -> None:
        if not row.get("kpi_id"):
            row["kpi_id"] = new_kpi_id()
        self._kpis.append(row)

    def update_kpi(self, kpi_id: str, updates: dict) -> bool:
        kpi = self.get_kpi(kpi_id)
        if kpi is None:
            return False
        kpi.update(updates)
        return True

    def delete_kpi(self, kpi_id: str) -> bool:
        before = len(self._kpis)
        self._kpis = [k for k in self._kpis if k.get("kpi_id") != kpi_id]
        return len(self._kpis) < before

    def list_data(self, company_code: str, kpi_id: Optional[str] = None, limit: int = 30) -> List[dict]:
        filtered = [d for d in self._data if d.get("company_code") == company_code]
        if kpi_id:
            filtered = [d for d in filtered if d.get("kpi_id") == kpi_id]
        filtered.sort(key=lambda x: str(x.get("recorded_at", "")), reverse=True)
        return filtered[:limit]

    def add_data(self, row: dict) -> None:
        if not row.get("entry_id"):
            row["entry_id"] = new_kpi_entry_id()
        self._data.append(row)
