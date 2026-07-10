"""Custom KPI router — owner-defined KPI configuration and manual data entry."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import CurrentUser, get_current_user
from app.dependencies import get_custom_kpis, get_tickets, get_machines
from app.repositories.base import CustomKpiRepository, MachineRepository, TicketRepository, new_kpi_id, new_kpi_entry_id
from app.services.dashboard_service import build_custom_kpi_values, compute_auto_insights

router = APIRouter(prefix="/vault/kpis")


class KpiConfigIn(BaseModel):
    kpi_name: str
    kpi_type: str = "manual"
    unit: str = ""
    target_value: str = ""
    warning_threshold: str = ""
    critical_threshold: str = ""
    cost_per_hour: str = ""
    display_order: int = 0


class KpiDataIn(BaseModel):
    kpi_id: str
    value: str


@router.get("")
def list_custom_kpis(
    user: CurrentUser = Depends(get_current_user),
    kpi_repo: CustomKpiRepository = Depends(get_custom_kpis),
    tickets: TicketRepository = Depends(get_tickets),
    machines: MachineRepository = Depends(get_machines),
):
    """Return all custom KPI configs with their current computed values."""
    configs = kpi_repo.list_kpis(user.company_code)
    data = kpi_repo.list_data(user.company_code)
    all_tickets = tickets.get_company_tickets(user.company_code)
    all_machines = machines.get_company_machines(user.company_code)
    auto_insights = compute_auto_insights(all_tickets, all_machines)

    open_tickets = sum(1 for t in all_tickets if t.get("status") == "Open")
    closed = [t for t in all_tickets if t.get("status") == "Closed"]
    avg_hours = 0.0
    if closed:
        h_sum, cnt = 0.0, 0
        for t in closed:
            try:
                h = float(t.get("hours_to_fix", 0))
                if h > 0:
                    h_sum += h
                    cnt += 1
            except (ValueError, TypeError):
                pass
        avg_hours = h_sum / cnt if cnt else 0.0

    base_kpis = {"open_tickets": open_tickets, "avg_hours_to_fix": round(avg_hours, 1)}
    values = build_custom_kpi_values(user.company_code, configs, data, auto_insights, base_kpis)

    return {
        "configs": configs,
        "values": values,
        "auto_insights": auto_insights,
    }


@router.post("")
def add_custom_kpi(
    body: KpiConfigIn,
    user: CurrentUser = Depends(get_current_user),
    kpi_repo: CustomKpiRepository = Depends(get_custom_kpis),
):
    """Add a new custom KPI config for the owner's company."""
    if user.role not in ("owner", "supervisor", "maintenance_head"):
        raise HTTPException(status_code=403, detail="only owner/supervisor can configure KPIs")

    existing = kpi_repo.list_kpis(user.company_code)
    if len(existing) >= 10:
        raise HTTPException(status_code=400, detail="max 10 custom KPIs per company")

    kpi_id = new_kpi_id()
    row = {
        "kpi_id": kpi_id,
        "company_code": user.company_code,
        "kpi_name": body.kpi_name,
        "kpi_type": body.kpi_type,
        "unit": body.unit,
        "target_value": body.target_value,
        "warning_threshold": body.warning_threshold,
        "critical_threshold": body.critical_threshold,
        "cost_per_hour": body.cost_per_hour,
        "display_order": body.display_order,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    kpi_repo.add_kpi(row)
    return {"kpi_id": kpi_id, "status": "created"}


@router.put("/{kpi_id}")
def update_custom_kpi(
    kpi_id: str,
    body: KpiConfigIn,
    user: CurrentUser = Depends(get_current_user),
    kpi_repo: CustomKpiRepository = Depends(get_custom_kpis),
):
    """Update an existing custom KPI config."""
    if user.role not in ("owner", "supervisor", "maintenance_head"):
        raise HTTPException(status_code=403, detail="only owner/supervisor can configure KPIs")

    kpi = kpi_repo.get_kpi(kpi_id)
    if not kpi or kpi.get("company_code") != user.company_code:
        raise HTTPException(status_code=404, detail="KPI not found")

    kpi_repo.update_kpi(kpi_id, body.dict(exclude_unset=True))
    return {"status": "updated"}


@router.delete("/{kpi_id}")
def delete_custom_kpi(
    kpi_id: str,
    user: CurrentUser = Depends(get_current_user),
    kpi_repo: CustomKpiRepository = Depends(get_custom_kpis),
):
    """Delete a custom KPI config."""
    user.assert_owner()

    kpi = kpi_repo.get_kpi(kpi_id)
    if not kpi or kpi.get("company_code") != user.company_code:
        raise HTTPException(status_code=404, detail="KPI not found")

    kpi_repo.delete_kpi(kpi_id)
    return {"status": "deleted"}


@router.post("/data")
def log_kpi_data(
    body: KpiDataIn,
    user: CurrentUser = Depends(get_current_user),
    kpi_repo: CustomKpiRepository = Depends(get_custom_kpis),
):
    """Log a manual KPI data entry (e.g. daily production count)."""
    kpi = kpi_repo.get_kpi(body.kpi_id)
    if not kpi or kpi.get("company_code") != user.company_code:
        raise HTTPException(status_code=404, detail="KPI not found")

    entry_id = new_kpi_entry_id()
    row = {
        "entry_id": entry_id,
        "company_code": user.company_code,
        "kpi_id": body.kpi_id,
        "value": body.value,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "recorded_by": user.user_id,
    }
    kpi_repo.add_data(row)
    return {"entry_id": entry_id, "status": "recorded"}


@router.get("/data/{kpi_id}")
def get_kpi_history(
    kpi_id: str,
    user: CurrentUser = Depends(get_current_user),
    kpi_repo: CustomKpiRepository = Depends(get_custom_kpis),
):
    """Return recent data entries for a specific KPI."""
    kpi = kpi_repo.get_kpi(kpi_id)
    if not kpi or kpi.get("company_code") != user.company_code:
        raise HTTPException(status_code=404, detail="KPI not found")

    return {"kpi_id": kpi_id, "entries": kpi_repo.list_data(user.company_code, kpi_id)}
