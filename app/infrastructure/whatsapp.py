"""WhatsApp Cloud API client — resilient version using the retry HTTP client.

Replaces app/whatsapp_client.py.  All external HTTP calls now go through
infrastructure.http_client.resilient_post / resilient_get which retries on
transient 429/5xx errors with exponential backoff.
"""

import mimetypes
from typing import List

from app import config
from app.infrastructure.http_client import resilient_get, resilient_post
from app.infrastructure.logging import get_logger

log = get_logger("turbofix.whatsapp")


def _graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{path}"


async def download_media(media_id: str) -> str:
    """Resolve a WhatsApp media ID → download → save to MEDIA_STORE_DIR.

    Returns the local file path as a string (consumed by the AI transcription step).
    Raises on any HTTP/network error after retries.
    """
    headers = {"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"}

    meta_resp = await resilient_get(_graph_url(media_id), headers=headers)
    meta = meta_resp.json()

    media_resp = await resilient_get(meta["url"], headers=headers)

    mime_type = meta.get("mime_type", "application/octet-stream")
    extension = mimetypes.guess_extension(mime_type.split(";")[0].strip()) or ""
    dest = config.MEDIA_STORE_DIR / f"{media_id}{extension}"
    dest.write_bytes(media_resp.content)
    log.info("whatsapp.media_downloaded", media_id=media_id, path=str(dest))
    return str(dest)


async def send_template_message(to: str, params: List[str]) -> None:
    """Send the pre-approved ticket notification template to `to`.

    Uses resilient_post — transient 429s (Meta rate limit) are retried with
    exponential backoff before raising, so a brief Meta outage no longer silently
    drops fan-out notifications.
    """
    headers = {
        "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": config.WHATSAPP_TICKET_TEMPLATE_NAME,
            "language": {"code": config.WHATSAPP_TICKET_TEMPLATE_LANGUAGE},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in params],
            }],
        },
    }
    await resilient_post(
        _graph_url(f"{config.WHATSAPP_PHONE_NUMBER_ID}/messages"),
        headers=headers,
        json=payload,
    )
    log.info("whatsapp.template_sent", to=to, template=config.WHATSAPP_TICKET_TEMPLATE_NAME)
