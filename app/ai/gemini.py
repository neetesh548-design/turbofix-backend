import base64
import json
from pathlib import Path

import httpx

from app import config
from app.ai.summarize import _SYSTEM_PROMPT, IssueBrief, _normalize_urgency

# Raw REST like the OpenAI modules (no SDK dependency). Gemini's generateContent
# handles both audio transcription (inline audio part) and JSON summarization,
# so one free-tier API key covers the whole AI layer.
_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# WhatsApp voice notes arrive as ogg/opus; the rest are here for completeness.
_AUDIO_MIME_TYPES = {
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".amr": "audio/amr",
}


def _url() -> str:
    return _GENERATE_URL.format(model=config.GEMINI_MODEL)


def _headers() -> dict:
    return {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}


def _response_text(resp_json: dict) -> str:
    return resp_json["candidates"][0]["content"]["parts"][0]["text"]


async def transcribe_audio(file_path: str) -> str:
    """Sends a downloaded voice note to Gemini inline and returns the plain-text
    transcript. Raises on any HTTP/network error - callers should catch and degrade
    gracefully rather than fail the whole webhook."""
    path = Path(file_path)
    mime_type = _AUDIO_MIME_TYPES.get(path.suffix.lower(), "audio/ogg")
    audio_b64 = base64.b64encode(path.read_bytes()).decode("ascii")

    payload = {
        "contents": [{
            "parts": [
                {"text": (
                    "Transcribe this factory-floor voice note verbatim into plain text. "
                    "Reply with only the transcript, no preamble or commentary. "
                    "If it isn't in English, transcribe it in its original language."
                )},
                {"inline_data": {"mime_type": mime_type, "data": audio_b64}},
            ]
        }]
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        resp.raise_for_status()
        return _response_text(resp.json()).strip()


async def summarize_issue(description: str) -> IssueBrief:
    """Calls Gemini to turn a raw issue description into a structured brief.
    Same prompt and output contract as the OpenAI path. Raises on any HTTP/
    network/parse error - callers should catch and degrade gracefully."""
    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": description}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        resp.raise_for_status()
        content = _response_text(resp.json())

    parsed = json.loads(content)
    return IssueBrief(
        likely_cause=str(parsed.get("likely_cause", "")).strip(),
        urgency=_normalize_urgency(parsed.get("urgency", "")),
        suggested_action=str(parsed.get("suggested_action", "")).strip(),
    )
