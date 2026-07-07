import re
from dataclasses import dataclass
from typing import Optional

# Matches the structured machine ID embedded in the QR-code-prefilled message,
# e.g. "Issue with TF-ACME3-M042: spindle noise" -> TF-ACME3-M042.
_MACHINE_ID_RE = re.compile(r"\bTF-([A-Za-z0-9]+)-([A-Za-z0-9]+)\b", re.IGNORECASE)


@dataclass
class ParsedTicket:
    machine_id: str
    company_code: str
    machine_code: str
    description: str


def parse_message(text: str) -> Optional[ParsedTicket]:
    """Extract the machine ID and issue description from an incoming message.

    Returns None if the text doesn't contain a recognizable TF-{company}-{machine} ID.
    """
    if not text:
        return None

    match = _MACHINE_ID_RE.search(text)
    if not match:
        return None

    company_code, machine_code = match.group(1).upper(), match.group(2).upper()
    machine_id = f"TF-{company_code}-{machine_code}"

    remainder = text[match.end():].strip()
    if remainder.startswith(":"):
        remainder = remainder[1:].strip()

    return ParsedTicket(
        machine_id=machine_id,
        company_code=company_code,
        machine_code=machine_code,
        description=remainder,
    )
