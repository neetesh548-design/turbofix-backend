import asyncio

from app import config
from app.ai import gemini, provider
from app.ai import summarize as openai_summarize
from app.ai.summarize import IssueBrief


def _set_keys(monkeypatch, ai_provider="auto", gemini_key="", openai_key=""):
    monkeypatch.setattr(config, "AI_PROVIDER", ai_provider)
    monkeypatch.setattr(config, "GEMINI_API_KEY", gemini_key)
    monkeypatch.setattr(config, "OPENAI_API_KEY", openai_key)


def test_auto_prefers_gemini_when_both_keys_set(monkeypatch):
    _set_keys(monkeypatch, "auto", gemini_key="g", openai_key="o")
    assert provider.active_provider() == "gemini"


def test_auto_falls_back_to_openai(monkeypatch):
    _set_keys(monkeypatch, "auto", openai_key="o")
    assert provider.active_provider() == "openai"


def test_auto_with_no_keys_is_disabled(monkeypatch):
    _set_keys(monkeypatch, "auto")
    assert provider.active_provider() == ""
    assert not provider.enabled()


def test_explicit_provider_without_its_key_is_disabled_not_fallthrough(monkeypatch):
    # AI_PROVIDER=gemini with only an OpenAI key must NOT silently use the paid path.
    _set_keys(monkeypatch, "gemini", openai_key="o")
    assert provider.active_provider() == ""
    _set_keys(monkeypatch, "openai", gemini_key="g")
    assert provider.active_provider() == ""


def test_dispatch_routes_to_active_provider(monkeypatch):
    _set_keys(monkeypatch, "auto", gemini_key="g")

    async def fake_gemini_summarize(description):
        return IssueBrief("via gemini", "Low", "noop")

    monkeypatch.setattr(gemini, "summarize_issue", fake_gemini_summarize)
    brief = asyncio.run(provider.summarize_issue("anything"))
    assert brief.likely_cause == "via gemini"

    _set_keys(monkeypatch, "auto", openai_key="o")

    async def fake_openai_summarize(description):
        return IssueBrief("via openai", "Low", "noop")

    monkeypatch.setattr(openai_summarize, "summarize_issue", fake_openai_summarize)
    brief = asyncio.run(provider.summarize_issue("anything"))
    assert brief.likely_cause == "via openai"
