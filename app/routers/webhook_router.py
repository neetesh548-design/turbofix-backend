"""Webhook router — WhatsApp webhook receive + verify.

This router is intentionally thin:
- Parse the incoming HTTP request.
- Return 200 OK to WhatsApp as fast as possible.
- Delegate all business logic to ticket_service.

No business logic lives here; that keeps this module trivially testable
(just check status codes and call counts, not business outcomes).
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response

from app.dependencies import get_machines, get_tickets
from app.repositories.base import MachineRepository, TicketRepository
from app.services import ticket_service
from app.sessions import SessionStore
from app import config
from app.infrastructure.logging import get_logger

log = get_logger("turbofix.webhook")

router = APIRouter()

# Module-level session store (one per process, same as before).
# In a future multi-replica deployment this would move to Redis.
_sessions = SessionStore()


def get_sessions() -> SessionStore:
    """Dependency that returns the module-level session store."""
    return _sessions


@router.get("/webhook")
def verify_webhook(request: Request):
    """Meta's one-time handshake when you register the webhook URL."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)


def _iter_messages(payload: dict):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                yield message


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    tickets: TicketRepository = Depends(get_tickets),
    machines: MachineRepository = Depends(get_machines),
):
    """Receive and dispatch an incoming WhatsApp message.

    Returns 200 immediately (WhatsApp requires a fast ack); all slow work
    (AI, fan-out) is dispatched as background tasks.
    """
    payload = await request.json()
    sessions = get_sessions()

    for message in _iter_messages(payload):
        phone = message.get("from", "")
        msg_type = message.get("type")

        if msg_type == "text":
            await ticket_service.handle_text_message(
                phone=phone,
                text=message.get("text", {}).get("body", ""),
                background_tasks=background_tasks,
                sessions=sessions,
                tickets=tickets,
                machines=machines,
            )
        elif msg_type == "audio":
            await ticket_service.handle_audio_message(
                phone=phone,
                media_id=message.get("audio", {}).get("id", ""),
                background_tasks=background_tasks,
                sessions=sessions,
                tickets=tickets,
                machines=machines,
            )
        else:
            log.info("webhook.unsupported_type", msg_type=msg_type, phone=phone)

    return Response(status_code=200)
