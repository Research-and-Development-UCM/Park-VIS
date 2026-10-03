"""Billing portal client.

All requests are authenticated via the HWID placed in the request body
— no Authorization header, no JWT. The portal's heartbeat endpoint
already accepts HWID-in-body as an alternative to JWT, so this works
seamlessly against the existing cloud API.
"""

from __future__ import annotations

from typing import Optional

import httpx

from .hwid import get_hwid
from .session import LocalSession, default_session


class BillingError(Exception):
    """Base exception for billing client errors."""


class BillingAuthError(BillingError):
    """Raised on 401 — the device is unknown to billing."""


class BillingDeviceNotFoundError(BillingError):
    """Raised on 404 from the portal — HWID not yet provisioned.

    This is NOT an error condition: it just means the user has not yet
    completed the activation flow. Callers should treat this as a
    normal pre-activation state, not a failure.
    """


class BillingServerError(BillingError):
    """Raised on any other non-2xx response."""


class BillingClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        session: Optional[LocalSession] = None,
        timeout: float = 15.0,
    ):
        from ..config import config

        self.base_url = (base_url or config.BILLING_PORTAL_URL or "").rstrip("/")
        self.session = session or default_session()
        self.timeout = timeout

    def _url(self, path: str) -> str:
        """Build the full URL for a billing API call.

        All calls go through the ``/api/v1`` prefix because that's what
        the cloud portal's nginx proxies to the FastAPI backend. The
        frontend itself uses the same prefix (see
        ``frontend/src/api/index.js``).

        ``PARK_VIS_BILLING_PORTAL_URL`` should point at the **frontend**
        URL (the one users visit), NOT the raw backend — in production
        that means ``https://billing.lotvulture.com``, not
        ``https://api.billing.lotvulture.com``.
        """
        if not self.base_url:
            raise BillingError("Billing portal URL is not configured")
        if not path.startswith("/"):
            path = "/" + path
        # Normalize: caller might pass "v1/...", "api/v1/...", or just a
        # bare endpoint like "heartbeat".  The portal's API is mounted
        # at ``/api/v1``; we route everything through there.
        if path.startswith("/v1/"):
            path = "/api" + path
        elif not path.startswith("/api/v1/"):
            path = "/api/v1" + path
        return self.base_url.rstrip("/") + path

    def _hwid_body(self, data: Optional[dict] = None) -> dict:
        body = dict(data or {})
        body.setdefault("hwid", get_hwid())
        return body

    async def _request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        use_hwid: bool = True,
    ) -> dict:
        url = self._url(path)
        body = self._hwid_body(data) if use_hwid else data
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if method == "GET":
                    resp = await client.get(url, params=body)
                elif method == "POST":
                    resp = await client.post(url, json=body)
                else:
                    raise BillingError(f"Unsupported method: {method}")
        # ConnectError and TimeoutException are both subclasses of
        # HTTPError, so the previous three-clause except chain had two
        # dead branches (caught by the broader HTTPError handler).
        except httpx.HTTPError as exc:
            raise BillingServerError(
                f"Billing request to {self.base_url} failed: {exc}"
            ) from exc

        if resp.status_code == 401:
            raise BillingAuthError(
                f"billing auth failed: {resp.text[:200] if hasattr(resp, 'text') else ''}"
            )
        if resp.status_code == 404:
            # The cloud returns 404 when the HWID hasn't been registered
            # yet (user hasn't activated). Modern cloud builds return
            # the specific code:
            #   {"error": {"code": "device_not_activated",
            #               "message": "Device not found. ..."}}
            # Older cloud builds (and FastAPI-default error envelopes)
            # use {"detail": "Device not found. ..."} or a generic
            # {"error": {"code": "not_found", ...}}. We check in this
            # order:
            #   1. specific code (preferred — survives message changes)
            #   2. message substring (back-compat for older clouds)
            try:
                body = resp.json() or {}
            except ValueError:
                body = {}

            err = body.get("error") or {}
            code = err.get("code", "") if isinstance(err, dict) else ""
            message = (
                (err.get("message") if isinstance(err, dict) else None)
                or body.get("detail")
                or "Device not found"
            )
            message_lower = message.lower()

            is_not_activated = (
                code == "device_not_activated"
                or ("device" in message_lower and "not found" in message_lower)
            )
            if is_not_activated:
                raise BillingDeviceNotFoundError(message)
            raise BillingServerError(
                f"billing 404: {resp.text[:200] if hasattr(resp, 'text') else ''}"
            )
        if resp.status_code >= 400:
            raise BillingServerError(
                f"billing {resp.status_code}: {resp.text[:200] if hasattr(resp, 'text') else ''}"
            )

        try:
            return resp.json()
        except ValueError:
            return {}

    async def heartbeat(self, camera_count: int) -> dict:
        body = {"camera_count": max(0, int(camera_count))}
        return await self._request("POST", "/heartbeat", data=body)

    async def forget_device(self) -> dict:
        """Delete this device from the billing portal.

        Called when the user clicks 'Disconnect' in the Billing page.
        Idempotent — billing returns ``already_gone: true`` if the
        device was already removed (e.g., user clicked twice).
        """
        return await self._request("POST", "/device/forget", data={})

    async def get_session(self) -> dict:
        return await self._request("GET", "/session")