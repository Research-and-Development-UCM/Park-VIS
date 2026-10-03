"""Mode enforcement — decides which license the inference engine should use.

Three modes:
- ``community``: bundle community model, lower accuracy, no license key.
- ``trial_commercial``: commercial model with a time-limited license.
- ``commercial``: commercial model under a paid subscription.

The on-prem engine always binds the license to the host's HWID via
the C++ ``VultureVision(license_key, instance_id=hwid)`` constructor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..logging_config import vulture_logger as logger
from .session import LocalSession


COMMUNITY = "community"
TRIAL_COMMERCIAL = "trial_commercial"
COMMERCIAL = "commercial"


def compute_mode(
    session: LocalSession,
    now: Optional[datetime] = None,
) -> str:
    """Decide which license mode the inference engine should use.

    The cloud billing portal is the source of truth for the user's
    entitlement.  The local ``LocalSession.mode`` reflects what the
    last heartbeat wrote, and the ``license_expires_at`` reflects
    when that entitlement ends.

    H4 audit fix: every commercial/trial path now checks
    ``is_active()`` so an expired entitlement immediately demotes
    back to community — the previous code returned the stored
    mode unconditionally, so a license that the cloud had revoked
    kept running for as long as the local session held the token.

    The community path is honoured unconditionally (the cloud says
    the account is community, we don't second-guess that).
    """
    current = now or datetime.now(timezone.utc)

    if session.mode == COMMUNITY:
        return COMMUNITY

    # Commercial / trial paths: the local expiry is the source of
    # truth for "is this license still valid?".  If the cloud
    # downgraded us, the heartbeat writes mode=community and we
    # take the community path above.  If the cloud kept us in
    # commercial but the expiry is in the past (payment lapsed,
    # firewall blocked the revocation heartbeat, etc.), we treat
    # the license as community and clear the token/expires
    # fields via ``enforce_expiry`` (called by the heartbeat and
    # by ``get_vulturevision``).
    if session.mode in (TRIAL_COMMERCIAL, COMMERCIAL):
        if not session.is_active(current):
            return COMMUNITY
        return session.mode

    # Legacy / uninitialised paths — only reached on the very
    # first load before any heartbeat has completed.
    if not session.is_active(current):
        return COMMUNITY
    if session.trial_expires_at and session.trial_expires_at > current:
        return TRIAL_COMMERCIAL
    if session.quota and session.quota > 0:
        return COMMERCIAL
    return COMMUNITY


def enforce_expiry(
    session: LocalSession,
    now: Optional[datetime] = None,
) -> bool:
    """If the session's commercial/trial license has expired,
    downgrade to community and clear the stored token/expiry.

    H4 audit fix: this is the single source of truth for "the
    license expired, stop handing it to the engine".  It is called
    by the heartbeat loop (after every successful response) and by
    ``inference.get_vulturevision`` (before resolving the license
    for the next inference call).  The 60-second re-check interval
    in ``get_vulturevision`` means a non-heartbeat path (e.g. a
    device that firewalls the billing portal after a subscription
    lapses) is caught within a minute of the expiry passing.

    Returns True if a downgrade happened, False otherwise.  Callers
    use the return value to decide whether to call
    ``inference.invalidate_config_cache()`` (the engine's bound
    license needs a re-init after a downgrade).
    """
    current = now or datetime.now(timezone.utc)

    if session.mode not in (TRIAL_COMMERCIAL, COMMERCIAL):
        return False

    if session.is_active(current):
        return False

    logger.warning(
        "billing enforce_expiry: '{mode}' license has expired "
        "(license_expires_at={exp}); downgrading to community and clearing token.",
        mode=session.mode,
        exp=session.license_expires_at,
    )
    session.mode = COMMUNITY
    session.license_token = None
    session.license_expires_at = None
    return True


def resolve_license(
    session: LocalSession,
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    """Return ``(license_key, instance_id)`` for the inference engine.

    When mode is ``community``, both are empty strings so the C++
    engine loads the bundled ``spot_detector.encs`` model.
    """
    mode = compute_mode(session, now)
    if mode == COMMUNITY:
        return "", ""
    return session.license_token or "", _hw_id()


def _hw_id() -> str:
    from .hwid import get_hwid
    return get_hwid()