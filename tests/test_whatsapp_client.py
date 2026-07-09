"""Tests for the WhatsApp infrastructure client — updated for the SOLID architecture."""

import asyncio

import pytest

from app import config
from app.infrastructure import whatsapp


class FakeResponse:
    def __init__(self, json_data=None, content=b""):
        self._json_data = json_data
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


class FakeAsyncClient:
    def __init__(self, urls_to_responses):
        self._responses = urls_to_responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        for key, response in self._responses.items():
            if key in url:
                return response
        raise AssertionError(f"unexpected URL requested: {url}")


def test_download_media_saves_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_STORE_DIR", tmp_path)
    monkeypatch.setattr(config, "WHATSAPP_ACCESS_TOKEN", "fake-token")

    media_id = "wamid.HBgLTEST"
    meta_response = FakeResponse(json_data={
        "url": "https://lookaside.fbsbx.com/whatsapp_media/fake",
        "mime_type": "audio/ogg; codecs=opus",
    })
    binary_response = FakeResponse(content=b"fake-audio-bytes")

    fake_client = FakeAsyncClient({
        media_id: meta_response,
        "lookaside.fbsbx.com": binary_response,
    })

    # The new infrastructure.whatsapp module uses resilient_get from http_client.
    # We patch httpx.AsyncClient since resilient_get uses it under the hood.
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: fake_client)

    path = asyncio.run(whatsapp.download_media(media_id))

    assert path.endswith(".oga") or path.endswith(".ogg") or ".oga" in path or ".ogg" in path
    with open(path, "rb") as f:
        assert f.read() == b"fake-audio-bytes"


class FakePostAsyncClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None, **kwargs):
        self.calls.append((url, headers, json))
        return FakeResponse(json_data={"messages": [{"id": "wamid.SENT"}]})


def test_send_template_message_posts_expected_payload(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_ACCESS_TOKEN", "fake-token")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(config, "WHATSAPP_API_VERSION", "v20.0")
    monkeypatch.setattr(config, "WHATSAPP_TICKET_TEMPLATE_NAME", "turbofix_new_ticket")
    monkeypatch.setattr(config, "WHATSAPP_TICKET_TEMPLATE_LANGUAGE", "en_US")

    fake_client = FakePostAsyncClient()
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: fake_client)

    asyncio.run(whatsapp.send_template_message(
        "919900011111", ["CNC Lathe 1", "T123", "brief", "High", "919900099999"]
    ))

    assert len(fake_client.calls) == 1
    url, headers, body = fake_client.calls[0]
    assert url == "https://graph.facebook.com/v20.0/1234567890/messages"
    assert headers["Authorization"] == "Bearer fake-token"
    assert body["to"] == "919900011111"
    assert body["template"]["name"] == "turbofix_new_ticket"
    assert body["template"]["language"] == {"code": "en_US"}
    assert body["template"]["components"][0]["parameters"] == [
        {"type": "text", "text": "CNC Lathe 1"},
        {"type": "text", "text": "T123"},
        {"type": "text", "text": "brief"},
        {"type": "text", "text": "High"},
        {"type": "text", "text": "919900099999"},
    ]
