"""Dashboard router — per-company KPI dashboard endpoint."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CurrentUser, get_current_user
from app.dependencies import get_machines, get_tickets, get_users
from app.repositories.base import MachineRepository, TicketRepository, UserRepository
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
