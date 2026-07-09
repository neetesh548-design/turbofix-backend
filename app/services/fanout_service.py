"""Fan-out service — notifies technicians and informed users about a ticket.

Extracted from fanout.py and wired to the resilient HTTP client (tenacity retry).
Each recipient send is independent — one failure never blocks the rest.
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


def _template_params(ticket: dict) -> list:
    brief = ticket.get("ai_summary") or ticket.get("description") or "(no description)"
    return [
        ticket.get("machine_name", ""),
        ticket.get("ticket_id", ""),
        brief,
        ticket.get("urgency") or "Medium",
        ticket.get("reporter_phone", ""),
    ]


async def notify_ticket(machine: dict, ticket: dict) -> None:
    """Fan out a finished ticket to its machine's assigned technician and informed users.

    Uses the resilient WhatsApp client (retry + backoff) rather than a bare httpx call.
    Each send is independent — a failure for one recipient is logged but doesn't block others.
    """
    from app.infrastructure import whatsapp  # imported here to avoid circular imports

    ticket_id = ticket.get("ticket_id")
    recipients = _recipients(machine)

    if not recipients:
        log.warning("fanout.no_recipients", ticket_id=ticket_id)
        return

    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        log.info("fanout.skipped", reason="no_whatsapp_credentials", ticket_id=ticket_id)
        return

    params = _template_params(ticket)
    for phone in recipients:
        try:
            await whatsapp.send_template_message(phone, params)
            log.info("fanout.sent", ticket_id=ticket_id, recipient=phone)
        except Exception as exc:
            log.error("fanout.failed", ticket_id=ticket_id, recipient=phone, error=str(exc))
