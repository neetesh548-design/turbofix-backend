"""Fan-out service — notifies technicians and informed users about tickets.

Each recipient send is independent — one failure never blocks the rest.
Handles both new-ticket notifications and closure notifications.
Role-tailored alerts: owner gets cost/urgency summary, supervisor gets production
impact, maintenance technician gets technical details + suggested action.
"""

from app import config
from app.infrastructure.logging import get_logger

log = get_logger("turbofix.fanout")


def _recipients(machine: dict) -> list:
    recipients = []
    if machine.get("assigned_technician_phone"):
        recipients.append(machine["assigned_technician_phone"])
    recipients.extend(machine.get("informed_phones", []))
    return recipients


def _all_recipients(machine: dict, ticket: dict) -> list:
    """All stakeholders + the original worker who reported the issue."""
    recipients = _recipients(machine)
    reporter = ticket.get("reporter_phone")
    if reporter and reporter not in recipients:
        recipients.append(reporter)
    return recipients


def _template_params(ticket: dict) -> list:
    brief = ticket.get("ai_summary") or ticket.get("description") or "(no description)"
    return [
        ticket.get("machine_name", ""),
        ticket.get("ticket_id", ""),
        brief,
        ticket.get("urgency") or "Medium",
        ticket.get("reporter_phone", ""),
    ]


def _role_tailored_brief(ticket: dict, phone: str, machine: dict) -> str:
    """Return a role-appropriate summary based on who's receiving it."""
    technician_phone = machine.get("assigned_technician_phone", "")
    is_technician = phone == technician_phone

    tech_summary = ticket.get("_technician_summary", "")
    owner_summary = ticket.get("_owner_summary", "")
    supervisor_summary = ticket.get("_supervisor_summary", "")
    default = ticket.get("ai_summary") or ticket.get("description") or "(no description)"

    if is_technician and tech_summary:
        return tech_summary
    if owner_summary and not is_technician:
        return owner_summary if phone != technician_phone else default
    if supervisor_summary:
        return supervisor_summary
    return default


def _closure_params(ticket: dict, closed_by_phone: str) -> list:
    return [
        ticket.get("machine_name", ""),
        ticket.get("ticket_id", ""),
        ticket.get("ai_summary") or ticket.get("description") or "(no description)",
        closed_by_phone,
    ]


async def notify_ticket(machine: dict, ticket: dict) -> None:
    """Fan out a finished ticket with role-tailored messages.

    - Assigned technician gets technical details + suggested action
    - Informed users (owner/supervisor) get their role-appropriate summaries
    - Falls back to the generic AI summary if role-specific ones aren't available
    """
    from app.infrastructure import whatsapp

    ticket_id = ticket.get("ticket_id")
    recipients = _recipients(machine)

    if not recipients:
        log.warning("fanout.no_recipients", ticket_id=ticket_id)
        return

    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        log.info("fanout.skipped", reason="no_whatsapp_credentials", ticket_id=ticket_id)
        return

    for phone in recipients:
        brief = _role_tailored_brief(ticket, phone, machine)
        params = [
            ticket.get("machine_name", ""),
            ticket.get("ticket_id", ""),
            brief,
            ticket.get("urgency") or "Medium",
            ticket.get("reporter_phone", ""),
        ]
        try:
            await whatsapp.send_template_message(phone, params)
            log.info("fanout.sent", ticket_id=ticket_id, recipient=phone, role_tailored=True)
        except Exception as exc:
            log.error("fanout.failed", ticket_id=ticket_id, recipient=phone, error=str(exc))


async def notify_closure(
    machine: dict,
    ticket: dict,
    closed_by_phone: str,
    translated_message: str | None = None,
    worker_language: str | None = None,
) -> None:
    """Notify all stakeholders + the worker that a ticket has been closed.

    If a translated_message is provided and worker_language differs from 'en',
    the worker gets the translated version while stakeholders get English.
    """
    from app.infrastructure import whatsapp

    ticket_id = ticket.get("ticket_id")
    all_phones = _all_recipients(machine, ticket)
    reporter_phone = ticket.get("reporter_phone")

    if not all_phones:
        log.warning("closure.no_recipients", ticket_id=ticket_id)
        return

    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        log.info("closure.skipped", reason="no_whatsapp_credentials", ticket_id=ticket_id)
        return

    params = _closure_params(ticket, closed_by_phone)
    machine_name = ticket.get("machine_name", "")

    for phone in all_phones:
        try:
            if phone == reporter_phone and translated_message and worker_language and worker_language != "en":
                await whatsapp.send_text_message(phone, translated_message)
            else:
                await whatsapp.send_closure_template(phone, params)
            log.info("closure.sent", ticket_id=ticket_id, recipient=phone)
        except Exception as exc:
            log.error("closure.failed", ticket_id=ticket_id, recipient=phone, error=str(exc))
