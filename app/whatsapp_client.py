import mimetypes
from typing import List

import httpx

from app import config


def _graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{path}"


async def download_media(media_id: str) -> str:
    """Resolves a WhatsApp media ID to its download URL, downloads the bytes, and
    saves them to MEDIA_STORE_DIR. Returns the local file path as a string."""
    headers = {"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"}
    async with httpx.AsyncClient() as client:
        meta_resp = await client.get(_graph_url(media_id), headers=headers)
        meta_resp.raise_for_status()
        meta = meta_resp.json()

        media_resp = await client.get(meta["url"], headers=headers)
        media_resp.raise_for_status()

    mime_type = meta.get("mime_type", "application/octet-stream")
    extension = mimetypes.guess_extension(mime_type.split(";")[0].strip()) or ""
    dest = config.MEDIA_STORE_DIR / f"{media_id}{extension}"
    dest.write_bytes(media_resp.content)
    return str(dest)


async def send_template_message(to: str, params: List[str]) -> None:
    """Sends the pre-approved WHATSAPP_TICKET_TEMPLATE_NAME template to `to`. Fan-out
    recipients (assigned technician / informed users) haven't messaged TurboFix
    themselves, so Meta requires an approved template rather than free-form text here.
    Raises on any HTTP/network error - callers should catch per-recipient so one
    failed send doesn't block the rest of the fan-out."""
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
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _graph_url(f"{config.WHATSAPP_PHONE_NUMBER_ID}/messages"),
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
