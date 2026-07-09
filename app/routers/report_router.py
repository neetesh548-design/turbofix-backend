"""Report router — on-demand and scheduled maintenance reports."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import CurrentUser, get_current_user
from app.dependencies import get_machines, get_tickets, get_users
from app.repositories.base import MachineRepository, TicketRepository, UserRepository
from app.services.report_service import format_report_text, generate_report

router = APIRouter(prefix="/vault")

VALID_PERIODS = {"daily", "weekly", "monthly", "ytd"}


@router.get("/reports/{period}")
def get_report(
    period: str,
    user: CurrentUser = Depends(get_current_user),
    tickets: TicketRepository = Depends(get_tickets),
    machines: MachineRepository = Depends(get_machines),
    users: UserRepository = Depends(get_users),
):
    """Generate and return a report for the authenticated user's company."""
    if period not in VALID_PERIODS:
        raise HTTPException(status_code=400, detail=f"Invalid period. Must be one of: {', '.join(VALID_PERIODS)}")

    company = users.get_company(user.company_code)
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    report = generate_report(
        company_code=user.company_code,
        company_name=company.get("company_name", ""),
        period=period,
        tickets_repo=tickets,
        machines_repo=machines,
    )
    report["formatted_text"] = format_report_text(report)
    return report


@router.post("/reports/{period}/send")
async def send_report(
    period: str,
    user: CurrentUser = Depends(get_current_user),
    tickets: TicketRepository = Depends(get_tickets),
    machines: MachineRepository = Depends(get_machines),
    users: UserRepository = Depends(get_users),
):
    """Generate a report and send it via WhatsApp to the authenticated user."""
    if period not in VALID_PERIODS:
        raise HTTPException(status_code=400, detail=f"Invalid period. Must be one of: {', '.join(VALID_PERIODS)}")

    company = users.get_company(user.company_code)
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    report = generate_report(
        company_code=user.company_code,
        company_name=company.get("company_name", ""),
        period=period,
        tickets_repo=tickets,
        machines_repo=machines,
    )
    text = format_report_text(report)

    from app import config
    from app.infrastructure import whatsapp

    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        return {"status": "skipped", "reason": "no_whatsapp_credentials", "report": text}

    user_record = users.get_by_id(user.user_id)
    phone = user_record.get("phone") if user_record else None
    if phone:
        try:
            await whatsapp.send_text_message(phone, text)
            return {"status": "sent", "recipient": phone}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Failed to send: {exc}")

    return {"status": "skipped", "reason": "no_phone_number", "report": text}
