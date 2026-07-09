"""Dashboard router — per-company KPI dashboard + root cause analysis endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CurrentUser, get_current_user
from app.dependencies import get_events, get_machines, get_tickets, get_users
from app.repositories.base import EventRepository, MachineRepository, TicketRepository, UserRepository
from app.services import ai_service
from app.services.dashboard_service import compute_kpis

router = APIRouter(prefix="/vault")


@router.get("/dashboard")
def get_dashboard(
    user: CurrentUser = Depends(get_current_user),
    tickets: TicketRepository = Depends(get_tickets),
    machines: MachineRepository = Depends(get_machines),
    users: UserRepository = Depends(get_users),
):
    """Return live KPI dashboard for the authenticated user's company."""
    company = users.get_company(user.company_code)
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    return compute_kpis(
        company_code=user.company_code,
        company_name=company.get("company_name", ""),
        tickets_repo=tickets,
        machines_repo=machines,
    )


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
