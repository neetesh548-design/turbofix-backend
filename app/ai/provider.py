from typing import List

from app import config
from app.ai import gemini
from app.ai import summarize as openai_summarize
from app.ai import transcribe as openai_transcribe
from app.ai.summarize import IssueBrief


def active_provider() -> str:
    """Resolves which AI backend to use: "gemini", "openai", or "" (AI layer off).
    An explicitly chosen provider whose key is missing counts as off - never
    silently fall through to a different (possibly paid) provider."""
    if config.AI_PROVIDER == "gemini":
        return "gemini" if config.GEMINI_API_KEY else ""
    if config.AI_PROVIDER == "openai":
        return "openai" if config.OPENAI_API_KEY else ""
    # auto: prefer the free-tier option
    if config.GEMINI_API_KEY:
        return "gemini"
    if config.OPENAI_API_KEY:
        return "openai"
    return ""


def enabled() -> bool:
    return bool(active_provider())


async def transcribe_audio(file_path: str) -> str:
    if active_provider() == "gemini":
        return await gemini.transcribe_audio(file_path)
    return await openai_transcribe.transcribe_audio(file_path)


async def summarize_issue(description: str) -> IssueBrief:
    if active_provider() == "gemini":
        return await gemini.summarize_issue(description)
    return await openai_summarize.summarize_issue(description)


async def analyze_image(file_path: str) -> str:
    if active_provider() == "gemini":
        return await gemini.analyze_image(file_path)
    raise NotImplementedError("Image analysis requires Gemini provider")


async def detect_language(text: str) -> str:
    if active_provider() == "gemini":
        return await gemini.detect_language(text)
    return "en"


async def translate_message(text: str, target_language: str) -> str:
    if active_provider() == "gemini":
        return await gemini.translate_message(text, target_language)
    return text


async def root_cause_analysis(machine_name: str, events: List[dict]) -> str:
    if active_provider() == "gemini":
        return await gemini.root_cause_analysis(machine_name, events)
    raise NotImplementedError("Root cause analysis requires Gemini provider")
