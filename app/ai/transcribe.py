from pathlib import Path

import httpx

from app import config

_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"


async def transcribe_audio(file_path: str) -> str:
    """Sends a downloaded voice note to OpenAI's transcription API and returns the
    plain-text transcript. Raises on any HTTP/network error - callers should catch
    and degrade gracefully rather than fail the whole webhook."""
    path = Path(file_path)
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
    data = {"model": config.OPENAI_TRANSCRIBE_MODEL}

    async with httpx.AsyncClient(timeout=60) as client:
        with open(path, "rb") as f:
            files = {"file": (path.name, f, "application/octet-stream")}
            resp = await client.post(_TRANSCRIPTION_URL, headers=headers, data=data, files=files)
        resp.raise_for_status()
        return resp.json()["text"].strip()
