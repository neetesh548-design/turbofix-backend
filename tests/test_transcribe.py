import asyncio

from app import config
from app.ai import transcribe


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.captured = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, data=None, files=None):
        self.captured = {"url": url, "headers": headers, "data": data, "files": files}
        return FakeResponse(json_data={"text": "  spindle is making a loud grinding noise  "})


def test_transcribe_audio_returns_stripped_text(tmp_path, monkeypatch):
    audio_file = tmp_path / "note.ogg"
    audio_file.write_bytes(b"fake audio bytes")

    monkeypatch.setattr(config, "OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(transcribe.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(transcribe.transcribe_audio(str(audio_file)))
    assert result == "spindle is making a loud grinding noise"
