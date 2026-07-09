"""Structured JSON logging for TurboFix, backed by structlog.

Every log event produces a JSON line with consistent keys that can be
filtered/searched in Railway's log dashboard:

    {"event": "ticket.created", "ticket_id": "T2026...", "company": "ACME3", ...}

Usage anywhere in the codebase:
    from app.infrastructure.logging import get_logger
    log = get_logger(__name__)
    log.info("ticket.created", ticket_id=ticket_id, machine_id=machine_id)
    log.error("ai.failed", ticket_id=ticket_id, error=str(exc))
"""

import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Call once at application startup (from main.py lifespan)."""

    # Route stdlib logging through structlog so third-party libraries
    # (httpx, gspread, uvicorn) also produce structured JSON.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "turbofix"):
    """Return a structlog logger bound to `name`.

    The returned logger behaves exactly like stdlib logging but emits JSON:
        log.info("event.name", key=value, ...)
    """
    return structlog.get_logger(name)
