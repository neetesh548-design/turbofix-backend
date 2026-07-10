"""Escalation service — notify the platform admin when a company registration goes unapproved.

Design:
  - Runs as an asyncio background task (started in main.py lifespan).
  - Every ESCALATION_CHECK_INTERVAL_SECONDS (default 3600 = 1 h) it reads all companies,
    finds those with approved=no and registered_at > APPROVAL_ESCALATION_HOURS ago, and
    sends one escalation email per company (each company gets at most one email per startup
    cycle, tracked in _already_notified).
  - Uses the same email_client as password reset so no new infrastructure is needed.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Set

from app import config, email_client
from app.infrastructure.logging import get_logger

log = get_logger("turbofix.escalation")

# In-memory set of company codes that have already been escalated this run.
# Resets on server restart — good enough for V1.
_already_notified: Set[str] = set()

ESCALATION_CHECK_INTERVAL_SECONDS = int(
    config.APPROVAL_ESCALATION_HOURS * 60 * 30  # half the escalation window per check
)
# Ensure we check at least every 30 minutes but no more than every hour
ESCALATION_CHECK_INTERVAL_SECONDS = max(1800, min(3600, ESCALATION_CHECK_INTERVAL_SECONDS))


def _is_approved(company: dict) -> bool:
    return str(company.get("approved") or "").strip().lower() in {"yes", "true", "1"}


def _parse_registered_at(value) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def run_escalation_check(users_repo) -> list[str]:
    """Synchronous core — check all companies and send escalation emails as needed.

    Returns a list of company codes that were escalated in this run (for testing).
    """
    escalated = []
    try:
        companies = users_repo.list_companies()
    except Exception as exc:
        log.error("escalation.list_companies_failed", error=str(exc))
        return escalated

    threshold = timedelta(hours=config.APPROVAL_ESCALATION_HOURS)
    now = datetime.now(timezone.utc)

    for c in companies:
        code = c.get("company_code", "")
        if not code:
            continue
        if _is_approved(c):
            continue
        if code in _already_notified:
            continue

        registered_at = _parse_registered_at(c.get("registered_at"))
        if registered_at is None:
            # No timestamp — skip rather than spam
            continue

        age = now - registered_at
        if age < threshold:
            continue

        # This company has been waiting longer than the threshold — escalate
        hours_waiting = int(age.total_seconds() / 3600)
        company_name = c.get("company_name", code)
        log.warning(
            "escalation.approval_overdue",
            company_code=code,
            company_name=company_name,
            hours_waiting=hours_waiting,
        )
        try:
            email_client.send_email(
                to=config.PLATFORM_ADMIN_EMAIL,
                subject=f"[TurboFix] Approval overdue: {company_name} ({code})",
                body=(
                    f"Hi TurboFix Admin,\n\n"
                    f"The following company registration has been waiting for approval "
                    f"for {hours_waiting} hour(s) and requires your attention:\n\n"
                    f"  Company Name : {company_name}\n"
                    f"  Company Code : {code}\n"
                    f"  Registered At: {registered_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                    f"Please log in to the admin console and approve or reject this request:\n"
                    f"  http://localhost:8000/admin\n\n"
                    f"This is an automated alert. It will not repeat for this company "
                    f"until the server is restarted.\n\n"
                    f"Regards,\nTurboFix Platform"
                ),
            )
            _already_notified.add(code)
            escalated.append(code)
        except Exception as exc:
            log.error("escalation.email_failed", company_code=code, error=str(exc))

    return escalated


async def escalation_loop(users_repo_factory) -> None:
    """Async background loop. users_repo_factory() must return a UserRepository instance."""
    log.info("escalation.loop_started", interval_seconds=ESCALATION_CHECK_INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(ESCALATION_CHECK_INTERVAL_SECONDS)
        try:
            repo = users_repo_factory()
            escalated = run_escalation_check(repo)
            if escalated:
                log.info("escalation.sent", count=len(escalated), codes=escalated)
        except Exception as exc:
            log.error("escalation.loop_error", error=str(exc))
