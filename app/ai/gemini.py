import base64
import json
from pathlib import Path
from typing import List

import httpx

from app import config
from app.ai.summarize import _SYSTEM_PROMPT, IssueBrief, _normalize_urgency

_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

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

_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
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
        owner_summary=str(parsed.get("owner_summary", "")).strip(),
        supervisor_summary=str(parsed.get("supervisor_summary", "")).strip(),
        technician_summary=str(parsed.get("technician_summary", "")).strip(),
    )


async def analyze_image(file_path: str) -> str:
    """Send a machine photo to Gemini and get a text description of visible issues."""
    path = Path(file_path)
    mime_type = _IMAGE_MIME_TYPES.get(path.suffix.lower(), "image/jpeg")
    img_b64 = base64.b64encode(path.read_bytes()).decode("ascii")

    payload = {
        "contents": [{
            "parts": [
                {"text": (
                    "You are a factory maintenance assistant. Analyze this machine photo. "
                    "Describe what you see: the machine/part condition, any visible damage, "
                    "wear, leaks, misalignment, or anomalies. Be concise and technical. "
                    "If nothing looks wrong, say so."
                )},
                {"inline_data": {"mime_type": mime_type, "data": img_b64}},
            ]
        }]
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        resp.raise_for_status()
        return _response_text(resp.json()).strip()


async def detect_language(text: str) -> str:
    """Detect the language and return an ISO 639-1 code."""
    payload = {
        "contents": [{
            "parts": [{"text": (
                "Detect the language of the following text and respond with ONLY "
                "the ISO 639-1 two-letter language code (e.g. 'en', 'hi', 'mr', 'ta', 'te'). "
                "Nothing else.\n\n"
                f"{text}"
            )}]
        }]
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        resp.raise_for_status()
        code = _response_text(resp.json()).strip().lower()[:2]
        return code if len(code) == 2 else "en"


async def translate_message(text: str, target_language: str) -> str:
    """Translate text to the target language."""
    lang_names = {
        "hi": "Hindi", "mr": "Marathi", "en": "English", "ta": "Tamil",
        "te": "Telugu", "kn": "Kannada", "gu": "Gujarati", "bn": "Bengali",
    }
    lang_name = lang_names.get(target_language, target_language)

    payload = {
        "contents": [{
            "parts": [{"text": (
                f"Translate the following text to {lang_name}. "
                "Reply with ONLY the translation, no preamble.\n\n"
                f"{text}"
            )}]
        }]
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        resp.raise_for_status()
        return _response_text(resp.json()).strip()


async def root_cause_analysis(machine_name: str, events: List[dict]) -> str:
    """Analyze a machine's event history for patterns and root causes."""
    events_text = "\n".join(
        f"- [{e.get('timestamp', '?')}] {e.get('event_type', '?')}: {e.get('description', '?')}"
        for e in events
    )

    payload = {
        "contents": [{
            "parts": [{"text": (
                f"You are a factory maintenance expert. Analyze the maintenance history "
                f"for machine '{machine_name}' below and identify:\n"
                "1. Recurring patterns or failure modes\n"
                "2. Likely root causes\n"
                "3. Recommended preventive actions\n"
                "4. Expected next failure (if predictable)\n\n"
                "Be concise and actionable. Use simple language a factory supervisor can understand.\n\n"
                f"Event history:\n{events_text}"
            )}]
        }]
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        resp.raise_for_status()
        return _response_text(resp.json()).strip()
