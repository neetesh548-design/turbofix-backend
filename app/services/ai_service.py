"""AI service — thin, provider-agnostic wrapper around the Gemini AI layer.

For the pilot, Gemini is the only provider (zero cost).  The OpenAI path
is preserved in app/ai/ but not wired up here unless AI_PROVIDER=openai.
This service is what the rest of the codebase calls — it never imports
gemini.py or openai modules directly.
"""

from typing import List

from app.ai import provider as _ai_provider
from app.ai.summarize import IssueBrief
from app.infrastructure.logging import get_logger

log = get_logger("turbofix.ai")


def ai_enabled() -> bool:
    """Return True if any AI provider is configured and ready."""
    return _ai_provider.enabled()


async def transcribe_audio(file_path: str) -> str:
    """Transcribe a downloaded voice note. Raises on error — callers must handle."""
    log.info("ai.transcribe.start", file_path=file_path)
    result = await _ai_provider.transcribe_audio(file_path)
    log.info("ai.transcribe.done", length=len(result))
    return result


async def summarize_issue(description: str) -> IssueBrief:
    """Summarize an issue description into a structured brief. Raises on error."""
    log.info("ai.summarize.start", description_len=len(description))
    brief = await _ai_provider.summarize_issue(description)
    log.info("ai.summarize.done", urgency=brief.urgency)
    return brief


async def analyze_image(file_path: str) -> str:
    """Analyze a machine photo and return a text description of what's visible."""
    log.info("ai.analyze_image.start", file_path=file_path)
    result = await _ai_provider.analyze_image(file_path)
    log.info("ai.analyze_image.done", length=len(result))
    return result


async def detect_language(text: str) -> str:
    """Detect the language of a text and return an ISO 639-1 code (e.g. 'hi', 'en', 'mr')."""
    log.info("ai.detect_language.start", text_len=len(text))
    result = await _ai_provider.detect_language(text)
    log.info("ai.detect_language.done", language=result)
    return result


async def translate_message(text: str, target_language: str) -> str:
    """Translate text to the target language (ISO 639-1 code)."""
    log.info("ai.translate.start", target=target_language, text_len=len(text))
    result = await _ai_provider.translate_message(text, target_language)
    log.info("ai.translate.done", length=len(result))
    return result


async def root_cause_analysis(machine_name: str, events: List[dict]) -> str:
    """Analyze a machine's event history and return root cause insights."""
    log.info("ai.root_cause.start", machine=machine_name, event_count=len(events))
    result = await _ai_provider.root_cause_analysis(machine_name, events)
    log.info("ai.root_cause.done", length=len(result))
    return result
