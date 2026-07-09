"""Resilient HTTP client with tenacity retry + exponential backoff.

Wraps httpx for all external API calls (Gemini, WhatsApp, anything else).
A transient 429 or 5xx from any of these APIs currently causes silent ticket loss.
This module prevents that by retrying with exponential backoff before giving up.

Usage:
    from app.infrastructure.http_client import resilient_post, resilient_get

    resp = await resilient_post(url, headers=headers, json=payload)
    resp = await resilient_get(url, headers=headers)
"""

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.infrastructure.logging import get_logger

log = get_logger("turbofix.http")

# Retry on transient errors: 429 Too Many Requests and 5xx server errors.
# Never retry 4xx client errors (bad request, auth failure) — those won't
# self-heal and would just waste quota.
def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)):
        return True
    return False


_retry_policy = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_is_retryable),
    reraise=True,  # re-raise the original exception after all retries exhausted
)


@_retry_policy
async def resilient_post(url: str, *, timeout: int = 30, **kwargs) -> httpx.Response:
    """POST to `url` with automatic retry on transient errors.

    All keyword arguments are forwarded to httpx.AsyncClient.post().
    Raises httpx.HTTPStatusError for non-retryable errors (4xx)
    or after all retry attempts are exhausted.
    """
    log.debug("http.post", url=url)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, **kwargs)
        resp.raise_for_status()
        return resp


@_retry_policy
async def resilient_get(url: str, *, timeout: int = 30, **kwargs) -> httpx.Response:
    """GET from `url` with automatic retry on transient errors."""
    log.debug("http.get", url=url)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, **kwargs)
        resp.raise_for_status()
        return resp
