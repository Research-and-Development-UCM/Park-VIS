"""Webhook channel — POST a JSON payload to a configured URL.

The channel is invoked by the dispatcher with the parsed channel
``config`` dict (so the dispatcher doesn't need to know about channel
internals) and the alert event payload.

The config shape is::

    {
        "url": "https://example.com/hook",
        "secret": "optional-shared-secret"  # if set, HMAC-SHA256 of body
    }

If ``secret`` is set, the request includes an ``X-Signature`` header
with ``sha256=<hex>`` of the JSON body.  This lets the receiver
verify the request actually came from this instance.

Returns a :class:`WebhookResult` indicating success/failure.  On
transport errors or non-2xx responses, ``success`` is False and
``error`` describes the failure for storage in ``alert_events.last_error``.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class WebhookResult:
    success: bool
    status_code: Optional[int] = None
    error: Optional[str] = None


async def deliver(config: dict, payload: dict, *, timeout: float = 10.0) -> WebhookResult:
    """POST ``payload`` (JSON-encoded) to ``config['url']``.

    The full alert event is wrapped in an envelope so receivers have
    a stable shape to parse::

        {
            "event_id": 123,
            "rule_id": 7,
            "fired_at": "2026-07-01T12:00:00+00:00",
            "trigger_type": "space_occupied",
            "rule_name": "VIP spaces",
            "data": { ... payload from the engine ... }
        }

    ``data`` is the engine-supplied payload dict; the envelope is added
    by this function so the channel stays generic.
    """
    url = (config or {}).get("url")
    if not url:
        return WebhookResult(success=False, error="missing url in channel config")

    secret = (config or {}).get("secret")
    body = json.dumps(payload, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Signature"] = f"sha256={sig}"

    try:
        # Use ``client.stream`` instead of ``client.post`` so the
        # response body isn't eagerly buffered into memory before
        # we see the status code.  A hostile (or just misconfigured)
        # server returning a 100MB 200 response would otherwise
        # OOM the dispatcher before the body is discarded.  The
        # 2xx path never reads the body at all; the error path
        # caps its read with ``_read_response_snippet``.
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", url, content=body, headers=headers
            ) as resp:
                if 200 <= resp.status_code < 300:
                    return WebhookResult(
                        success=True, status_code=resp.status_code
                    )
                # Truncate the response body so a hostile server
                # can't fill the last_error column with megabytes
                # of HTML, and can't OOM the dispatcher's worker
                # by returning a 100MB body.  Cap on the
                # content-length header first, then stream in
                # chunks so we never read more than
                # ``MAX_BODY_SNIPPET_BYTES`` either way.
                snippet = await _read_response_snippet(resp)
                return WebhookResult(
                    success=False,
                    status_code=resp.status_code,
                    error=f"http {resp.status_code}: {snippet}",
                )
    except httpx.HTTPError as exc:
        return WebhookResult(success=False, error=f"transport: {exc!s}")


MAX_BODY_SNIPPET_BYTES = 4096
SNIPPET_DISPLAY_BYTES = 200


async def _read_response_snippet(resp: httpx.Response) -> str:
    content_length = resp.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_BODY_SNIPPET_BYTES:
                return f"<body too large: {content_length} bytes>"
        except ValueError:
            pass
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        buf.extend(chunk)
        if len(buf) >= MAX_BODY_SNIPPET_BYTES:
            break
    return bytes(buf[:SNIPPET_DISPLAY_BYTES]).decode("utf-8", errors="replace")
