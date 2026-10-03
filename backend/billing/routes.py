"""FastAPI routes exposing billing status to the on-prem frontend.

All routes require the local JWT (the on-prem admin UI's auth) plus the
``manage_billing`` permission. The billing portal itself is reached via
HWID only — never via these routes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import database, models
from ..config import config
from ..logging_config import vulture_logger as logger
from .client import (
    BillingAuthError,
    BillingClient,
    BillingDeviceNotFoundError,
    BillingError,
    BillingServerError,
)
from .enforcer import compute_mode
from .hwid import get_hwid
from .session import (
    LocalSession,
    default_session,
    safe_save,
    safe_update_from_heartbeat,
    session_update_lock,
)


router = APIRouter(prefix="/api/billing", tags=["Billing"])


# Use the same OAuth2 scheme that the rest of the on-prem app uses, but
# never auto-error — we want to handle 401 ourselves inside each route.
_billing_oauth = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


REQUIRED_PERMISSION = "manage_billing"


async def _require_billing_user(
    request: Request, db: Session = Depends(database.get_db)
) -> models.User:
    """Resolve the current on-prem user AND require ``manage_billing``.

    Reimplemented here (instead of importing from app.py) to avoid the
    circular import that ``app.py`` → ``billing.routes`` → ``app.py``
    would create. Mirrors the behavior of ``backend.auth.get_auth_user``
    plus a permission check.
    """
    import jwt as _jwt
    from ..auth import SECRET as _SECRET, ALGORITHM as _ALGO
    from ..auth import hash_key

    user = None
    token = request.headers.get("Authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:]
    elif request.headers.get("X-API-Key"):
        key = request.headers.get("X-API-Key")
        h = hash_key(key)
        db_key = db.query(models.APIKey).filter_by(hashed_key=h).first()
        if db_key and db_key.creator:
            user = db_key.creator

    if user is None and token:
        try:
            payload = _jwt.decode(token, _SECRET, algorithms=[_ALGO])
            username = payload.get("sub")
            if username:
                user = (
                    db.query(models.User).filter_by(username=username).first()
                )
        except _jwt.PyJWTError:
            user = None

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # Admins get every permission; otherwise check the granular list.
    if not user.is_admin:
        from .. import auth as _auth
        perms = _auth.parse_permissions(user)
        if REQUIRED_PERMISSION not in perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {REQUIRED_PERMISSION}",
            )
    return user


def _db():
    yield from database.get_db()


class ActivateCallbackBody(BaseModel):
    license_token: str
    trial_expires_at: Optional[str] = None


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _status_payload(session: LocalSession) -> dict:
    mode = compute_mode(session)
    return {
        "mode": mode,
        "email": session.email,
        "trial_expires_at": _isoformat(session.trial_expires_at),
        "license_expires_at": _isoformat(session.license_expires_at),
        # Explicit grace-period end date (from
        # ``camera_quota.grace_period_ends_at``).  The cloud tracks
        # this separately from the capped license TTL so the
        # frontend can show "service ends on <date>" during grace
        # even if a later state (e.g. trial) would otherwise win the
        # cap.
        "subscription_expires_at": _isoformat(session.subscription_expires_at),
        "quota": session.quota,
        # ``device_quota`` is the seat count the cloud has allocated
        # to THIS device.  After the first heartbeat the cloud
        # syncs this to ``this_device_cameras``.  Surfacing it lets
        # the dashboard show "your device is allocated N seats"
        # without re-computing on the client.
        "device_quota": session.device_quota,
        "this_device_cameras": session.this_device_cameras,
        "other_devices_cameras": session.other_devices_cameras,
        "device_count": session.device_count,
        "last_heartbeat_at": _isoformat(session.last_heartbeat_at),
        "last_heartbeat_status": session.last_heartbeat_status,
        "hwid": get_hwid(),
        "billing_portal_url": config.BILLING_PORTAL_URL or "",
        "heartbeat_interval_seconds": config.HEARTBEAT_INTERVAL_SECONDS,
    }


@router.get("/status")
def get_status(
    request: Request,
    db: Session = Depends(_db),
    user: models.User = Depends(_require_billing_user),
):
    session = default_session()
    return _status_payload(session)


class ConnectUrlBody(BaseModel):
    # Frontend's current browser address (origin). When the on-prem
    # frontend is served by Vite on localhost:5173 but the API is
    # proxied to localhost:8000, the server-side ``request.base_url``
    # points at the API port — which the browser can't navigate to.
    # The frontend sends its real address here so the callback URL
    # always matches what the browser can actually reach.
    return_to: Optional[str] = None


@router.get("/connect-url")
def get_connect_url(
    request: Request,
    db: Session = Depends(_db),
    user: models.User = Depends(_require_billing_user),
):
    """Build the activation URL.

    The ``return_to`` query param lets the frontend override the
    callback URL with the user's actual browser address. This matters
    in dev: the Vite server runs on :5173 and proxies API calls to the
    FastAPI backend on :8000, so the server-side ``request.base_url``
    points at :8000 even though the browser is on :5173.
    """
    if not config.BILLING_PORTAL_URL:
        raise HTTPException(
            status_code=400,
            detail="PARK_VIS_BILLING_PORTAL_URL is not configured",
        )

    hwid = get_hwid()
    return_to = str(request.base_url).rstrip("/") + "/billing/callback"
    url = (
        f"{config.BILLING_PORTAL_URL.rstrip('/')}/connect"
        f"?hwid={hwid}&return_to={return_to}"
    )
    return {"url": url, "hwid": hwid, "return_to": return_to}


@router.post("/connect-url")
def get_connect_url_post(
    body: ConnectUrlBody,
    request: Request,
    db: Session = Depends(_db),
    user: models.User = Depends(_require_billing_user),
):
    """POST variant of ``/connect-url`` so the frontend can override
    the ``return_to`` with its actual browser origin.
    """
    if not config.BILLING_PORTAL_URL:
        raise HTTPException(
            status_code=400,
            detail="PARK_VIS_BILLING_PORTAL_URL is not configured",
        )

    hwid = get_hwid()
    if body.return_to:
        return_to = body.return_to.rstrip("/") + "/billing/callback"
    else:
        return_to = str(request.base_url).rstrip("/") + "/billing/callback"
    url = (
        f"{config.BILLING_PORTAL_URL.rstrip('/')}/connect"
        f"?hwid={hwid}&return_to={return_to}"
    )
    return {"url": url, "hwid": hwid, "return_to": return_to}


@router.post("/disconnect-url")
def get_disconnect_url_post(
    body: ConnectUrlBody,
    request: Request,
    db: Session = Depends(_db),
    user: models.User = Depends(_require_billing_user),
):
    """POST variant of ``/disconnect-url`` to construct the disconnect URL
    pointing to the billing portal's /disconnect endpoint.
    """
    if not config.BILLING_PORTAL_URL:
        raise HTTPException(
            status_code=400,
            detail="PARK_VIS_BILLING_PORTAL_URL is not configured",
        )

    hwid = get_hwid()
    if body.return_to:
        # Redirect back to the /billing/callback callback with action=unlink
        return_to = body.return_to.rstrip("/") + "/billing/callback?action=unlink"
    else:
        return_to = str(request.base_url).rstrip("/") + "/billing/callback?action=unlink"
    url = (
        f"{config.BILLING_PORTAL_URL.rstrip('/')}/disconnect"
        f"?hwid={hwid}&return_to={return_to}"
    )
    return {"url": url, "hwid": hwid, "return_to": return_to}


@router.post("/activate-callback")
async def activate_callback(
    body: ActivateCallbackBody,
    db: Session = Depends(_db),
    user: models.User = Depends(_require_billing_user),
):
    """Called after the billing portal redirects back to
    ``/billing/callback?decryption_key=<token>&trial_expires_at=<iso>``.

    Stores the license locally, then ALWAYS fires one heartbeat so the
    server's mode/quota/email are reconciled with billing — even on
    error. The 209-cameras-from-stale-cache bug was partly caused by
    this path passing ``camera_count=0``; we now pass the real DB
    count (excluding disabled cameras) so billing sees the truth
    immediately.
    """
    if not body.license_token:
        raise HTTPException(status_code=400, detail="license_token is required")

    session = default_session()
    async with session_update_lock():
        session.license_token = body.license_token
        # Fallback expiry: the immediate post-activate heartbeat is
        # supposed to fill this in. But if that heartbeat fails (network
        # blip, transient 502, "no payment method" surrogate, etc.) the
        # old code left ``license_expires_at = None`` →
        # ``LocalSession.is_active`` returned False → ``compute_mode``
        # dropped the user to community within ~60s of a successful
        # activation screen. We now seed the fallback from
        # ``trial_expires_at`` if present (so trial users get the trial
        # window) or 14 days (the cloud's license TTL) as a safety net.
        # The heartbeat, when successful, overwrites this with the
        # server-authoritative value.
        fallback_expiry: Optional[datetime]
        if body.trial_expires_at:
            try:
                fallback_expiry = datetime.fromisoformat(body.trial_expires_at)
                if fallback_expiry.tzinfo is None:
                    fallback_expiry = fallback_expiry.replace(tzinfo=timezone.utc)
            except ValueError:
                fallback_expiry = datetime.now(timezone.utc) + timedelta(days=14)
        else:
            fallback_expiry = datetime.now(timezone.utc) + timedelta(days=14)
        session.license_expires_at = fallback_expiry
        if body.trial_expires_at:
            try:
                session.trial_expires_at = datetime.fromisoformat(body.trial_expires_at)
                if session.trial_expires_at.tzinfo is None:
                    session.trial_expires_at = session.trial_expires_at.replace(
                        tzinfo=timezone.utc
                    )
            except ValueError:
                session.trial_expires_at = None
        session.save()

    # Always try to fire a heartbeat so we get fresh quota/mode/email
    # and so billing sees our real camera count immediately. Even when
    # the portal isn't configured we still save the license; the
    # background loop will sync once the operator sets the URL.
    if config.BILLING_PORTAL_URL:
        client = BillingClient(session=session)
        try:
            resp = await client.heartbeat(_total_camera_count())
            # Use the locked helper so the background loop can't
            # interleave an older response over this fresh one and
            # swap a freshly-stored token out from under us.
            await safe_update_from_heartbeat(session, resp)
        except BillingDeviceNotFoundError:
            async with session_update_lock():
                session.last_heartbeat_at = datetime.now(timezone.utc)
                session.last_heartbeat_status = "not_activated"
                session.save()
            logger.info(
                "activate-callback: billing returned 404 — license stored "
                "locally; will reconcile on next heartbeat."
            )
        except BillingAuthError:
            async with session_update_lock():
                session.last_heartbeat_at = datetime.now(timezone.utc)
                session.last_heartbeat_status = "auth_failed"
                session.save()
            logger.warning(
                "activate-callback: billing 401 — license stored locally; "
                "next heartbeat will retry."
            )
        except (BillingServerError, BillingError) as exc:
            # Transient failure: keep the license + fallback expiry we
            # just stored. The next scheduled heartbeat (typically
            # within minutes) fills in the authoritative
            # ``license_expires_at``. The previous code cleared the
            # token implicitly via the None fallback, which silently
            # downgraded the user to community within 60s — see the
            # audit notes.
            async with session_update_lock():
                session.last_heartbeat_at = datetime.now(timezone.utc)
                session.last_heartbeat_status = "error"
                session.save()
            logger.warning("activate-callback: heartbeat failed: {e}", e=exc)
            # Still return success — the license IS stored locally. The
            # user can manually retry via "Send Heartbeat Now".

    _invalidate_inference_cache()
    return {"status": "ok", **_status_payload(session)}


@router.post("/heartbeat/trigger")
async def trigger_heartbeat(
    db: Session = Depends(_db),
    user: models.User = Depends(_require_billing_user),
):
    """Force an immediate outbound heartbeat to billing."""
    if not config.BILLING_PORTAL_URL:
        raise HTTPException(
            status_code=400,
            detail="PARK_VIS_BILLING_PORTAL_URL is not configured",
        )

    session = default_session()
    client = BillingClient(session=session)
    try:
        total_cams = _total_camera_count()
        resp = await client.heartbeat(total_cams)
    except BillingDeviceNotFoundError as exc:
        # HWID not registered with billing — typically because the
        # user just clicked "Unlink & Cancel" and the cloud deleted
        # this device via forget_device. ``code: "device_not_activated"``
        # is the definitive signal that the device is not on any
        # account, so wipe ALL licensing data so the user is forced
        # to reactivate.  Without this, the inference engine keeps
        # running on commercial mode forever off the stale cached
        # token, and every subsequent heartbeat re-fails with 404.
        #
        # The activate-callback path has its own dedicated handler
        # below that intentionally does NOT clear the license —
        # there, a 404 is a transient race during first-time
        # activation and the license was just set by the user's
        # portal flow. Here in the manual "Sync Now" path the user
        # is asking the cloud about an existing device, so a 404
        # means the cloud has forgotten us.
        async with session_update_lock():
            session.mark_device_forgotten()
            session.save()
        _invalidate_inference_cache()
        raise HTTPException(
            status_code=404,
            detail=(
                "This device hasn't been activated yet. Click 'Activate License' "
                "to open the billing portal and complete activation."
            ),
        )
    except BillingAuthError:
        async with session_update_lock():
            session.last_heartbeat_at = datetime.now(timezone.utc)
            session.last_heartbeat_status = "auth_failed"
            session.save()
        raise HTTPException(status_code=401, detail="Billing rejected this device")
    except (BillingServerError, BillingError) as exc:
        async with session_update_lock():
            session.last_heartbeat_at = datetime.now(timezone.utc)
            session.last_heartbeat_status = "error"
            session.save()
        logger.warning("billing heartbeat failed: {e}", e=exc)
        raise HTTPException(status_code=502, detail=str(exc))

    # Use the locked helpers so a concurrently-running background
    # heartbeat can't interleave its older response over this fresh
    # one and swap a freshly-stored token out from under us.
    await safe_update_from_heartbeat(session, resp)
    _invalidate_inference_cache()
    return _status_payload(session)


@router.post("/logout")
async def logout(
    db: Session = Depends(_db),
    user: models.User = Depends(_require_billing_user),
):
    """Disconnect: clear local session AND remove the device from the
    billing portal so the user stops being billed for that seat.

    Order matters: we tell billing to forget us FIRST, then clear the
    local cache. If billing rejects, we surface that error and leave
    the local session intact so the user can retry.
    """
    if config.BILLING_PORTAL_URL:
        session = default_session()
        client = BillingClient(session=session)
        try:
            await client.forget_device()
        except BillingDeviceNotFoundError:
            # Device already gone from billing — that's fine.
            pass
        except BillingAuthError:
            # Billing has no record of us — also fine.
            pass
        except (BillingServerError, BillingError) as exc:
            # Don't clear local cache if we couldn't tell billing.
            logger.warning("billing forget_device failed: {e}", e=exc)
            raise HTTPException(
                status_code=502,
                detail=(
                    "Could not unlink this device from billing: "
                    f"{exc}. Please retry, or check the Billing Portal "
                    "directly to remove the device."
                ),
            )

    session = default_session()
    async with session_update_lock():
        session.clear()
        session.save()
    _invalidate_inference_cache()
    return {"status": "ok"}


def _invalidate_inference_cache() -> None:
    """Force the inference engine to re-read license/HWID within its
    60-second config-poll cycle."""
    try:
        from .. import inference
        inference._vv_config["last_check"] = 0
    except Exception:
        pass


def _total_camera_count() -> int:
    """Real camera count for the heartbeat, read from the DB.

    The scheduler's ``_state_cache`` can contain phantom entries from
    deleted cameras, so we never read from it for the heartbeat. The
    DB is the source of truth.

    Only counts cameras with ``is_enabled=True``. Disabled cameras
    are not actively processing video, so they should not occupy a
    licensed seat. Test cameras are included.
    """
    db = database.SessionLocal()
    try:
        return (
            db.query(models.Camera)
            .filter(models.Camera.is_enabled.is_(True))
            .count()
        )
    finally:
        db.close()