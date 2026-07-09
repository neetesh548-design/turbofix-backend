"""Password-reset flow (email link). Runs entirely against the local xlsx store with
EMAIL_PROVIDER=console; the outbound email is captured by monkeypatching
email_client.send_email, and the reset token is parsed out of the captured link -
exactly the path a real user's browser would take."""

import re
from urllib.parse import unquote

import pytest

from app import email_client
from tests.conftest import ACME_OWNER, login


@pytest.fixture
def sent_emails(monkeypatch):
    """Capture every email the backend tries to send instead of logging/sending it."""
    box = []
    monkeypatch.setattr(email_client, "send_email",
                        lambda to, subject, body: box.append({"to": to, "subject": subject, "body": body}))
    return box


def _token_from(email_body: str) -> str:
    match = re.search(r"[?&]token=(\S+)", email_body)
    assert match, f"no reset token in email body:\n{email_body}"
    return unquote(match.group(1))


def _request_reset(client, email: str, sent_emails) -> str:
    resp = client.post("/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200
    # The response never says whether the account exists...
    assert "if an account" in resp.json()["message"].lower()
    # ...but a real account triggers exactly one email whose link carries the token.
    return _token_from(sent_emails[-1]["body"])


def test_forgot_password_unknown_email_sends_nothing_but_looks_identical(vault_client, sent_emails):
    resp = vault_client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert resp.status_code == 200
    assert "if an account" in resp.json()["message"].lower()
    assert sent_emails == []  # no enumeration: unknown address gets no email


def test_full_reset_flow_lets_user_log_in_with_new_password(vault_client, sent_emails):
    email, old_password = ACME_OWNER
    token = _request_reset(vault_client, email, sent_emails)

    resp = vault_client.post("/auth/reset-password", json={"token": token, "new_password": "BrandNew@2027"})
    assert resp.status_code == 200

    # New password works...
    assert vault_client.post("/auth/login", json={"identifier": email, "password": "BrandNew@2027"}).status_code == 200
    # ...and the old one no longer does.
    assert vault_client.post("/auth/login", json={"identifier": email, "password": old_password}).status_code == 401


def test_reset_token_is_single_use(vault_client, sent_emails):
    email, _ = ACME_OWNER
    token = _request_reset(vault_client, email, sent_emails)

    first = vault_client.post("/auth/reset-password", json={"token": token, "new_password": "FirstNew@2027"})
    assert first.status_code == 200
    # The same link a second time is rejected - the password fingerprint it was bound
    # to no longer matches, so it (and every other outstanding link) is dead.
    second = vault_client.post("/auth/reset-password", json={"token": token, "new_password": "SecondNew@2027"})
    assert second.status_code == 400


def test_reset_rejects_garbage_token(vault_client):
    resp = vault_client.post("/auth/reset-password", json={"token": "not-a-real-token", "new_password": "Whatever@2027"})
    assert resp.status_code == 400


def test_reset_enforces_min_password_length(vault_client, sent_emails):
    email, _ = ACME_OWNER
    token = _request_reset(vault_client, email, sent_emails)
    resp = vault_client.post("/auth/reset-password", json={"token": token, "new_password": "short"})
    assert resp.status_code == 400


def test_login_token_cannot_be_used_as_reset_token(vault_client):
    # An access token must never be accepted by /auth/reset-password (purpose claim).
    access_token = login(vault_client, *ACME_OWNER)
    resp = vault_client.post("/auth/reset-password", json={"token": access_token, "new_password": "Hijack@2027"})
    assert resp.status_code == 400
