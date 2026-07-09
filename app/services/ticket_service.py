"""Ticket service — all business logic for creating and processing tickets.

This is where the heavy lifting from main.py now lives.  The route handler
(webhook_router.py) only parses the HTTP request and calls these methods.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import BackgroundTasks

from app import config
from app.infrastructure.logging import get_logger
from app.parser import parse_message
from app.repositories.base import MachineRepository, TicketRepository
from app.services import ai_service, fanout_service
from app.sessions import Session, SessionStore

log = get_logger("turbofix.ticket")

_PLACEHOLDER_DESCRIPTION = "(no description provided)"


def _merge_description(existing: str, transcript: str) -> str:
    if not existing or existing == _PLACEHOLDER_DESCRIPTION:
        return transcript
    return f"{existing} | Voice note: {transcript}"


async def _summarize_and_store(
    ticket_id: str,
    description: str,
    tickets: TicketRepository,
    new_description: Optional[str] = None,
) -> None:
    """Run AI summary for a ticket and write it back.

    Any failure (missing API key, network error, bad response) is logged and
    swallowed — a ticket must never fail to log just because AI is unavailable.
    """
    if not ai_service.ai_enabled():
        log.info("ai.skipped", reason="not_configured", ticket_id=ticket_id)
        return
    try:
        brief = await ai_service.summarize_issue(description)
        tickets.update_ai_fields(
            ticket_id,
            ai_summary=brief.as_ai_summary(),
            urgency=brief.urgency,
            description=new_description,
        )
        log.info("ai.stored", ticket_id=ticket_id, urgency=brief.urgency)
    except Exception as exc:
        log.error("ai.summarize_failed", ticket_id=ticket_id, error=str(exc))


async def _notify_fanout(
    machine_id: str,
    ticket_id: str,
    tickets: TicketRepository,
    machines: MachineRepository,
) -> None:
    """Fan out a finished ticket to technician + informed users."""
    machine = machines.get(machine_id)
    ticket = tickets.get(ticket_id)
    if machine is None or ticket is None:
        log.warning("fanout.missing_data", ticket_id=ticket_id, machine_id=machine_id)
        return
    await fanout_service.notify_ticket(machine, ticket)


async def finish_text_ticket(
    phone: str,
    machine_id: str,
    ticket_id: str,
    description: str,
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
) -> None:
    """Background tail for a text-triggered ticket: AI summary + fan-out."""
    await _summarize_and_store(ticket_id, description, tickets)
    await _notify_fanout(machine_id, ticket_id, tickets, machines)
    sessions.mark_notified(phone)


async def handle_text_message(
    phone: str,
    text: str,
    background_tasks: BackgroundTasks,
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
) -> None:
    """Create a ticket from an incoming text message and optionally kick off background work."""
    parsed = parse_message(text)
    if parsed is None:
        log.info("message.no_machine_id", phone=phone)
        return

    machine = machines.get(parsed.machine_id)
    if machine is None:
        log.warning("message.unknown_machine", machine_id=parsed.machine_id, phone=phone)
        return

    ticket_id = tickets.next_ticket_id()
    tickets.append({
        "ticket_id": ticket_id,
        "machine_id": parsed.machine_id,
        "company_code": machine["company_code"],
        "machine_name": machine["machine_name"],
        "reported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "reporter_phone": phone,
        "description": parsed.description or _PLACEHOLDER_DESCRIPTION,
        "ai_summary": "",
        "urgency": "",
        "status": "Open",
        "closed_at": "",
        "hours_to_fix": "",
        "voice_note_media_id": "",
    })
    sessions.open(phone, ticket_id, parsed.machine_id)
    log.info("ticket.created", ticket_id=ticket_id, machine_id=parsed.machine_id, phone=phone)

    if parsed.description:
        background_tasks.add_task(
            finish_text_ticket, phone, parsed.machine_id, ticket_id,
            parsed.description, sessions, tickets, machines,
        )


async def finish_audio_ticket(
    phone: str,
    session: Session,
    media_id: str,
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
) -> None:
    """Background tail for a voice-note-triggered ticket: transcribe + summarize + fan-out."""
    from app.infrastructure import whatsapp  # lazy import to avoid circular dependency

    if ai_service.ai_enabled():
        try:
            local_path = await whatsapp.download_media(media_id)
            transcript = await ai_service.transcribe_audio(local_path)
            existing = tickets.get(session.ticket_id)
            existing_desc = existing["description"] if existing else ""
            merged = _merge_description(existing_desc, transcript)
            await _summarize_and_store(session.ticket_id, merged, tickets, new_description=merged)
        except Exception as exc:
            log.error("transcription.failed", ticket_id=session.ticket_id, error=str(exc))
    else:
        log.info("transcription.skipped", reason="ai_disabled", ticket_id=session.ticket_id)

    if not session.notified:
        await _notify_fanout(session.machine_id, session.ticket_id, tickets, machines)
        sessions.mark_notified(phone)


async def handle_audio_message(
    phone: str,
    media_id: str,
    background_tasks: BackgroundTasks,
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
) -> None:
    """Attach a voice note to the active session's ticket and kick off background work."""
    session = sessions.get(phone)
    if session is None:
        log.warning("audio.no_session", phone=phone, media_id=media_id)
        return

    tickets.attach_voice_note(session.ticket_id, media_id)
    log.info("audio.attached", ticket_id=session.ticket_id, media_id=media_id)

    background_tasks.add_task(
        finish_audio_ticket, phone, session, media_id, sessions, tickets, machines,
    )


async def sweep_expired_unnotified(
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
) -> None:
    """Fire a fallback fan-out for any session that expired without being notified."""
    for phone, session in sessions.sweep_expired_unnotified():
        log.info(
            "sweep.fallback_fanout",
            ticket_id=session.ticket_id,
            machine_id=session.machine_id,
        )
        await _notify_fanout(session.machine_id, session.ticket_id, tickets, machines)
