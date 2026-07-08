import asyncio
import base64
import json

from app import config
from app.ai import gemini


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def gemini_reply(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class CapturingClient:
    captured = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        CapturingClient.captured = {"url": url, "headers": headers, "json": json}
        return FakeResponse(json_data=self.reply())

    @staticmethod
    def reply():
        return gemini_reply("  spindle is making a loud grinding noise  ")


def test_transcribe_audio_sends_inline_audio_and_strips_text(tmp_path, monkeypatch):
    audio_file = tmp_path / "note.ogg"
    audio_file.write_bytes(b"fake audio bytes")

    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr(gemini.httpx, "AsyncClient", CapturingClient)

    result = asyncio.run(gemini.transcribe_audio(str(audio_file)))
    assert result == "spindle is making a loud grinding noise"

    sent = CapturingClient.captured
    assert config.GEMINI_MODEL in sent["url"]
    assert sent["headers"]["x-goog-api-key"] == "fake-gemini-key"
    inline = sent["json"]["contents"][0]["parts"][1]["inline_data"]
    assert inline["mime_type"] == "audio/ogg"
    assert base64.b64decode(inline["data"]) == b"fake audio bytes"


def test_summarize_issue_parses_json_and_normalizes_urgency(monkeypatch):
    class SummaryClient(CapturingClient):
        @staticmethod
        def reply():
            return gemini_reply(json.dumps({
                "likely_cause": "worn spindle bearing",
                "urgency": "high",
                "suggested_action": "inspect and replace the bearing",
            }))

    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr(gemini.httpx, "AsyncClient", SummaryClient)

    brief = asyncio.run(gemini.summarize_issue("spindle making loud noise"))

    assert brief.likely_cause == "worn spindle bearing"
    assert brief.urgency == "High"
    assert brief.suggested_action == "inspect and replace the bearing"

    sent = CapturingClient.captured
    assert sent["json"]["generationConfig"]["response_mime_type"] == "application/json"
    assert sent["json"]["contents"][0]["parts"][0]["text"] == "spindle making loud noise"


def test_summarize_issue_defaults_unexpected_urgency_to_medium(monkeypatch):
    class WeirdUrgencyClient(CapturingClient):
        @staticmethod
        def reply():
            return gemini_reply(json.dumps({
                "likely_cause": "unclear",
                "urgency": "critical!!",
                "suggested_action": "investigate",
            }))

    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setattr(gemini.httpx, "AsyncClient", WeirdUrgencyClient)

    brief = asyncio.run(gemini.summarize_issue("something weird"))
    assert brief.urgency == "Medium"
