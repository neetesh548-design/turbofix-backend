import logging

from app import whatsapp_client

logger = logging.getLogger("turbofix")


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
    """Notifies a machine's assigned technician and informed users about a ticket,
    one WhatsApp template message per recipient. Each send is independent - a failure
    for one recipient is logged and doesn't block the others or fail the ticket."""
    recipients = _recipients(machine)
    if not recipients:
        logger.warning(
            "no assigned/informed phones for ticket %s, nothing to fan out to",
            ticket.get("ticket_id"),
        )
        return

    params = _template_params(ticket)
    for phone in recipients:
        try:
            await whatsapp_client.send_template_message(phone, params)
            logger.info("fan-out sent to %s for ticket %s", phone, ticket.get("ticket_id"))
        except Exception:
            logger.exception(
                "fan-out failed for %s on ticket %s", phone, ticket.get("ticket_id")
            )
