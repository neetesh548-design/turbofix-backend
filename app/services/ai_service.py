"""AI service — thin, provider-agnostic wrapper around the Gemini AI layer.

For the pilot, Gemini is the only provider (zero cost).  The OpenAI path
is preserved in app/ai/ but not wired up here unless AI_PROVIDER=openai.
This service is what the rest of the codebase calls — it never imports
gemini.py or openai modules directly.
"""

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
