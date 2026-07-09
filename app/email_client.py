"""Pluggable outbound email (Phase 5 - password reset).

Mirrors the local/sheets and local/gcs splits used elsewhere: EMAIL_PROVIDER picks
the backend at call time.
  - "console" (default): logs the message instead of sending it, so local dev and the
    test suite run the full forgot-password flow with no email account or network.
  - "smtp": sends via any SMTP server (SendGrid/SES/Mailgun/Gmail app-password) using
    the stdlib - no third-party SDK, no extra dependency to install.

Only send_email() is public; the router never cares which provider is active.
"""

import logging
import smtplib
import sys
from email.message import EmailMessage

from app import config

logger = logging.getLogger("turbofix.email")


def _send_console(to: str, subject: str, body: str) -> None:
    # Print straight to stdout (like Django's console email backend) rather than via
    # the logging module: console mode exists so a developer can *see* the reset link
    # in their terminal, and a custom logger's INFO records don't reliably surface
    # under uvicorn's log config. Flushed so it isn't buffered behind the response.
    print(
        f"\n----- TurboFix email (console mode; set EMAIL_PROVIDER=smtp to send for real) -----\n"
        f"To: {to}\nSubject: {subject}\n\n{body}\n"
        f"-------------------------------------------------------------------------------\n",
        file=sys.stdout, flush=True,
    )


def _send_smtp(to: str, subject: str, body: str) -> None:
    if not config.SMTP_HOST:
        raise RuntimeError("EMAIL_PROVIDER=smtp but SMTP_HOST is not set")
    msg = EmailMessage()
    msg["From"] = config.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
        smtp.starttls()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)


def send_email(to: str, subject: str, body: str) -> None:
    """Send (or, in console mode, log) a plain-text email. Failures are logged and
    swallowed: a broken SMTP config must never turn into a 500 that tells the caller
    whether an account existed - the forgot-password endpoint always responds the same
    way regardless."""
    try:
        if config.EMAIL_PROVIDER == "smtp":
            _send_smtp(to, subject, body)
        else:
            _send_console(to, subject, body)
    except Exception:  # noqa: BLE001 - deliberately never propagates to the caller
        logger.exception("[email] failed to send to=%s", to)
