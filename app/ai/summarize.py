import json
from dataclasses import dataclass

import httpx

from app import config

_CHAT_URL = "https://api.openai.com/v1/chat/completions"

_VALID_URGENCIES = {"Low", "Medium", "High"}

_SYSTEM_PROMPT = (
    "You are TurboFix's maintenance triage assistant for factory machines. "
    "Given a worker's description of a machine issue (which may be a rough voice-note "
    "transcript), respond with a JSON object with exactly these keys: "
    '"likely_cause" (a short technical guess at the root cause), '
    '"urgency" (one of "Low", "Medium", "High"), '
    '"suggested_action" (a short, concrete first step for the technician), '
    '"owner_summary" (1-2 sentences for the factory owner: urgency level, estimated production impact, cost risk), '
    '"supervisor_summary" (1-2 sentences for the supervisor: which team/person should respond, production line impact), '
    '"technician_summary" (1-2 sentences for the maintenance technician: technical diagnosis, specific tools/parts needed, step-by-step first action). '
    "Be concise - each field should be one or two short sentences."
)


@dataclass
class IssueBrief:
    likely_cause: str
    urgency: str
    suggested_action: str
    owner_summary: str = ""
    supervisor_summary: str = ""
    technician_summary: str = ""

    def as_ai_summary(self) -> str:
        return f"Likely cause: {self.likely_cause} | Suggested action: {self.suggested_action}"


def _normalize_urgency(value: str) -> str:
    value = (value or "").strip().capitalize()
    return value if value in _VALID_URGENCIES else "Medium"


async def summarize_issue(description: str) -> IssueBrief:
    """Calls OpenAI to turn a raw issue description into a structured brief.
    Raises on any HTTP/network/parse error - callers should catch and degrade
    gracefully rather than fail the whole webhook."""
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.OPENAI_CHAT_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": description},
        ],
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_CHAT_URL, headers=headers, json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

    parsed = json.loads(content)
    return IssueBrief(
        likely_cause=str(parsed.get("likely_cause", "")).strip(),
        urgency=_normalize_urgency(parsed.get("urgency", "")),
        suggested_action=str(parsed.get("suggested_action", "")).strip(),
        owner_summary=str(parsed.get("owner_summary", "")).strip(),
        supervisor_summary=str(parsed.get("supervisor_summary", "")).strip(),
        technician_summary=str(parsed.get("technician_summary", "")).strip(),
    )
