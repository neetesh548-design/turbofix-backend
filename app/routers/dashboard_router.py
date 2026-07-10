"""Dashboard router — per-company KPI dashboard + root cause analysis endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CurrentUser, get_current_user, Role
from app.dependencies import get_custom_kpis, get_events, get_machines, get_tickets, get_users
from app.repositories.base import CustomKpiRepository, EventRepository, MachineRepository, TicketRepository, UserRepository
from app.services import ai_service
from app.services.dashboard_service import build_custom_kpi_values, compute_kpis

router = APIRouter(prefix="/vault")


@router.get("/dashboard")
def get_dashboard(
    user: CurrentUser = Depends(get_current_user),
    tickets: TicketRepository = Depends(get_tickets),
    machines: MachineRepository = Depends(get_machines),
    users: UserRepository = Depends(get_users),
    kpi_repo: CustomKpiRepository = Depends(get_custom_kpis),
):
    """Return live KPI dashboard for the authenticated user's company."""
    company = users.get_company(user.company_code)
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    supervisor_id = user.user_id if user.role == Role.SUPERVISOR.value else None

    result = compute_kpis(
        company_code=user.company_code,
        company_name=company.get("company_name", ""),
        tickets_repo=tickets,
        machines_repo=machines,
        supervisor_id=supervisor_id,
    )

    kpi_configs = kpi_repo.list_kpis(user.company_code)
    if kpi_configs:
        kpi_data = kpi_repo.list_data(user.company_code)
        result["custom_kpis"] = build_custom_kpi_values(
            user.company_code, kpi_configs, kpi_data,
            result.get("auto_insights", {}), result.get("kpis", {}),
        )
    else:
        result["custom_kpis"] = []

    if user.role == Role.OWNER.value:
        company_users = users.get_company_users(user.company_code)
        all_machines = machines.get_company_machines(user.company_code)
        
        supervisors_map = []
        assigned_machine_ids = set()
        
        for u in company_users:
            if u.get("role") == Role.SUPERVISOR.value:
                sup_id = u["user_id"]
                sup_machines = [
                    {
                        "machine_id": m["machine_id"],
                        "machine_name": m["machine_name"],
                        "location": m["location"],
                        "has_open_tickets": bool(m.get("has_open_tickets")),
                    }
                    for m in all_machines
                    if m.get("supervisor_id") == sup_id
                ]
                for m in sup_machines:
                    assigned_machine_ids.add(m["machine_id"])
                supervisors_map.append({
                    "supervisor_id": sup_id,
                    "name": u["name"],
                    "phone": u["phone"],
                    "email": u["email"],
                    "machines": sup_machines
                })
        
        unassigned_machines = [
            {
                "machine_id": m["machine_id"],
                "machine_name": m["machine_name"],
                "location": m["location"],
                "has_open_tickets": bool(m.get("has_open_tickets")),
            }
            for m in all_machines
            if m["machine_id"] not in assigned_machine_ids
        ]
        
        result["supervisors"] = supervisors_map
        result["unassigned_machines"] = unassigned_machines

    return result


@router.get("/machines/{machine_id}/events")
def get_machine_events(
    machine_id: str,
    user: CurrentUser = Depends(get_current_user),
    machines: MachineRepository = Depends(get_machines),
    events: EventRepository = Depends(get_events),
):
    """Return the full event history for a machine (scoped to user's company)."""
    machine = machines.get(machine_id.upper())
    if machine is None or machine.get("company_code") != user.company_code:
        raise HTTPException(status_code=404, detail="machine not found")
    return {"machine_id": machine_id.upper(), "events": events.get_machine_events(machine_id.upper())}


@router.get("/machines/{machine_id}/root-cause")
async def get_root_cause_analysis(
    machine_id: str,
    user: CurrentUser = Depends(get_current_user),
    machines: MachineRepository = Depends(get_machines),
    events: EventRepository = Depends(get_events),
):
    """Run AI root cause analysis on a machine's full event history."""
    machine = machines.get(machine_id.upper())
    if machine is None or machine.get("company_code") != user.company_code:
        raise HTTPException(status_code=404, detail="machine not found")

    if not ai_service.ai_enabled():
        raise HTTPException(status_code=503, detail="AI service not configured")

    machine_events = events.get_machine_events(machine_id.upper())
    if not machine_events:
        return {"machine_id": machine_id.upper(), "analysis": "No events recorded yet for this machine."}

    analysis = await ai_service.root_cause_analysis(
        machine.get("machine_name", machine_id),
        machine_events,
    )
    return {"machine_id": machine_id.upper(), "analysis": analysis}
