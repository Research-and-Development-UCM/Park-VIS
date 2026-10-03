"""Local license session storage.

File-backed JSON cache of everything the on-prem app needs to talk to
the inference engine. No JWT — the HWID is the sole credential, and
the heartbeat is what keeps this fresh.

Concurrency note: ``default_session()`` returns a process-wide
singleton. Two async paths mutate it concurrently — the background
heartbeat loop in ``scheduler.py`` and the manual
``/api/billing/heartbeat/trigger`` route. Both used to call
``session.update_from_heartbeat(resp)`` and ``session.save()`` with no
synchronization. ``save()`` itself is atomic (``.tmp`` + ``os.replace``)
but the in-memory dict mutations weren't locked: an older response
could overwrite a newer ``license_token`` and a "cloud says community"
downgrade could be reverted by an in-flight older "commercial"
response. We now serialize all mutations through
``session_update_lock()`` for callers that go through
``safe_update_from_heartbeat()`` / ``safe_save()``.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .paths import session_path


# Module-level lock guarding the singleton ``LocalSession`` mutation.
# Hold this around any (read-mutate-save) sequence involving the shared
# session; the background loop and manual sync route both go through
# the ``safe_*`` wrappers below so callers shouldn't need to grab it
# directly. Use a single lock — there's only one session.
_session_lock = asyncio.Lock()


@dataclass
class LocalSession:
    license_token: Optional[str] = None
    license_expires_at: Optional[datetime] = None
    trial_expires_at: Optional[datetime] = None
    # The cloud's explicit grace-period end date (from
    # ``camera_quota.grace_period_ends_at``).  Distinct from
    # ``license_expires_at``: the latter is the capped TTL of the
    # license token (earliest of trial/grace/probation/normal) and
    # matches ``subscription_expires_at`` only when the grace period
    # is the active state limit.  The frontend uses this field to
    # render the grace-period banner's "service ends on <date>" text
    # without having to infer which state is active.
    subscription_expires_at: Optional[datetime] = None
    mode: str = "community"
    quota: int = 0
    email: Optional[str] = None
    last_heartbeat_at: Optional[datetime] = None
    last_heartbeat_status: str = "never"
    # Camera pool breakdown from the most recent heartbeat. These are
    # what the user sees on the License & Billing page — broken out so
    # they can see "this device uses N, other devices use M, pool size
    # is P" at a glance.
    this_device_cameras: int = 0
    other_devices_cameras: int = 0
    device_count: int = 0
    # This device's seat count as reported by the cloud.  The cloud
    # syncs ``Device.seat_count`` to the user's CameraQuota on the
    # first heartbeat, so ``device_quota`` ends up equal to
    # ``this_device_cameras`` once the device is fully initialized.
    # Kept as a separate field so the on-prem can distinguish "pool
    # size for this user" (``quota``) from "seats allocated to this
    # device" (``device_quota``) — useful for the auto-bump/auto-credit
    # flow where the cloud adjusts the pool to match the device count.
    device_quota: int = 0

    _file: Path = field(default_factory=session_path, repr=False)

    def save(self) -> None:
        data = {
            "license_token": self.license_token,
            "license_expires_at": _iso(self.license_expires_at),
            "trial_expires_at": _iso(self.trial_expires_at),
            "subscription_expires_at": _iso(self.subscription_expires_at),
            "mode": self.mode,
            "quota": self.quota,
            "email": self.email,
            "last_heartbeat_at": _iso(self.last_heartbeat_at),
            "last_heartbeat_status": self.last_heartbeat_status,
            "this_device_cameras": self.this_device_cameras,
            "other_devices_cameras": self.other_devices_cameras,
            "device_count": self.device_count,
            "device_quota": self.device_quota,
        }
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: serialize to ``.tmp`` then ``os.replace``
            # so a crash mid-write can't truncate ``session.json`` to
            # an empty / partial file. The next ``load()`` would
            # otherwise treat the file as corrupt, fall back to
            # defaults, and silently downgrade the customer to
            # community mode. ``os.replace`` is atomic on POSIX and
            # on Windows when the target is on the same volume.
            tmp = self._file.with_suffix(self._file.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._file)
        except OSError:
            pass

    def load(self) -> bool:
        try:
            if not self._file.exists():
                return False
            data = json.loads(self._file.read_text())
        except (OSError, json.JSONDecodeError):
            return False

        self.license_token = data.get("license_token")
        self.license_expires_at = _parse(data.get("license_expires_at"))
        self.trial_expires_at = _parse(data.get("trial_expires_at"))
        self.subscription_expires_at = _parse(data.get("subscription_expires_at"))
        self.mode = data.get("mode") or "community"
        self.quota = _safe_int(data.get("quota"))
        self.email = data.get("email")
        self.last_heartbeat_at = _parse(data.get("last_heartbeat_at"))
        self.last_heartbeat_status = data.get("last_heartbeat_status") or "never"
        self.this_device_cameras = _safe_int(data.get("this_device_cameras"))
        self.other_devices_cameras = _safe_int(data.get("other_devices_cameras"))
        self.device_count = _safe_int(data.get("device_count"))
        self.device_quota = _safe_int(data.get("device_quota"))
        return True

    def clear(self) -> None:
        try:
            if self._file.exists():
                self._file.unlink()
        except OSError:
            pass
        self.license_token = None
        self.license_expires_at = None
        self.trial_expires_at = None
        self.subscription_expires_at = None
        self.mode = "community"
        self.quota = 0
        self.email = None
        self.last_heartbeat_at = None
        self.last_heartbeat_status = "never"
        self.this_device_cameras = 0
        self.other_devices_cameras = 0
        self.device_count = 0
        self.device_quota = 0

    def mark_device_forgotten(self, now: Optional[datetime] = None) -> None:
        """Wipe ALL licensing data and put the session into a clean
        "device not registered" state.

        Called by the heartbeat 404 paths in ``trigger_heartbeat``
        and the scheduled ``_send_one_heartbeat`` when the cloud
        returns ``code: "device_not_activated"`` — the definitive
        signal that this HWID is not registered to any account.
        Without this wipe, the inference engine keeps using the
        stale ``license_token`` it had cached before the user
        clicked Unlink & Cancel, and the user has no way to
        recover (every subsequent heartbeat would re-fail with 404
        and re-set the same status).

        Differs from ``clear()`` in two ways:
          - Keeps ``last_heartbeat_at`` set to ``now()`` so the UI's
            "Last Sync" timestamp stays fresh.
          - Sets ``last_heartbeat_status`` to ``"not_activated"``
            (not ``"never"``) so the frontend renders the
            "Activate License" prompt rather than a generic
            "never synced" state.
        """
        current = now or datetime.now(timezone.utc)
        self.license_token = None
        self.license_expires_at = None
        self.trial_expires_at = None
        self.subscription_expires_at = None
        self.mode = "community"
        self.quota = 0
        self.email = None
        self.last_heartbeat_at = current
        self.last_heartbeat_status = "not_activated"
        self.this_device_cameras = 0
        self.other_devices_cameras = 0
        self.device_count = 0
        self.device_quota = 0

    def is_active(self, now: Optional[datetime] = None) -> bool:
        if not self.license_token or not self.license_expires_at:
            return False
        current = now or datetime.now(timezone.utc)
        return self.license_expires_at > current

    def update_from_heartbeat(self, data: dict) -> bool:
        """Apply a heartbeat response.  Returns True if a license
        downgrade was detected (so callers can invalidate the
        inference engine's license cache).

        H5 audit fix: every field check uses ``"key" in data``
        (key-presence) instead of ``data.get("key")`` (truthy guard).
        The old code could not clear ``license_token`` / ``email`` /
        ``license_expires_at`` by sending ``None`` or ``""`` — the
        truthy guard skipped the assignment and the local stale value
        persisted.  Now the cloud can revoke a token, clear an email,
        or expire a license by sending ``None``.

        H6 audit fix: the token-clear on downgrade is idempotent.
        Previously the check was ``if new_mode == "community" and
        self.mode != "community"`` (transition only), so if a token
        was injected by ``activate_callback`` while the session was
        already community, a subsequent ``mode: community`` heartbeat
        did NOT clear it.  Now we always clear token + expiry when
        the cloud says community, regardless of the previous mode.
        """
        downgraded = False

        if "license_token" in data:
            # Key-presence: None / "" / "abc" all overwrite.  This is
            # what enables token revocation from the cloud.
            new_token = data["license_token"] or None
            self.license_token = new_token

        if "license_expires_at" in data:
            self.license_expires_at = _parse(data["license_expires_at"])

        if "trial_expires_at" in data:
            self.trial_expires_at = _parse(data["trial_expires_at"])

        if "subscription_expires_at" in data:
            # Explicit grace-period end date from the cloud.  We
            # track it as a separate field from ``license_expires_at``
            # so the frontend can render the grace-period banner
            # even if the cloud's later heartbeat-capping logic
            # changes ``license_expires_at`` away from the
            # grace-period end.
            self.subscription_expires_at = _parse(data["subscription_expires_at"])

        if "mode" in data:
            new_mode = data["mode"] or "community"
            # H6 fix: always clear token when community, regardless
            # of the previous mode (idempotent — safe to call repeatedly
            # and the second time still has the same effect).
            if new_mode == "community":
                if self.license_token is not None or self.license_expires_at is not None:
                    downgraded = True
                self.license_token = None
                self.license_expires_at = None
            self.mode = new_mode

        if "quota" in data:
            self.quota = _safe_int(data["quota"])
        if "email" in data:
            # Key-presence — allows the cloud to clear an email by
            # sending None.
            self.email = data["email"] or None
        if "this_device_cameras" in data:
            self.this_device_cameras = _safe_int(data["this_device_cameras"])
        if "other_devices_cameras" in data:
            self.other_devices_cameras = _safe_int(data["other_devices_cameras"])
        if "device_count" in data:
            self.device_count = _safe_int(data["device_count"])
        if "device_quota" in data:
            # ``device_quota`` is the seat count the cloud has
            # allocated to THIS device.  We use key-presence (not a
            # truthy guard) so the cloud can reset it to 0 by sending
            # ``0`` explicitly — the same pattern as the other
            # numeric fields above.
            self.device_quota = _safe_int(data["device_quota"])
        self.last_heartbeat_at = datetime.now(timezone.utc)
        self.last_heartbeat_status = (
            data.get("status_code") or data.get("status") or "ok"
        )
        return downgraded


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_int(value) -> int:
    """Coerce ``value`` to ``int`` without raising on a corrupt
    session.json. The previous ``int(data.get("quota") or 0)`` raised
    ``ValueError`` on a non-numeric string like ``"abc"`` — ``"abc"``
    is truthy so the ``or 0`` fallback didn't fire — and propagated
    out of ``load()`` into ``default_session()`` callers, sometimes
    blocking backend startup.
    """
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


_default: Optional[LocalSession] = None


def default_session() -> LocalSession:
    global _default
    if _default is None:
        _default = LocalSession()
        _default.load()
    return _default


def session_update_lock() -> asyncio.Lock:
    """Single asyncio lock guarding mutations of the shared session.

    Callers that drive the background heartbeat loop or the manual
    sync route hold this lock around their (read-mutate-save) sequence
    so an older heartbeat response can't overwrite a newer one's
    ``license_token`` / ``mode`` (and so a "community" downgrade
    isn't reverted by an in-flight "commercial" response).

    Returns the same lock for every caller — there's only one
    session, so one lock is correct.
    """
    return _session_lock


async def safe_update_from_heartbeat(session: "LocalSession", data: dict) -> bool:
    """Lock around ``update_from_heartbeat`` so concurrent paths
    don't interleave and overwrite each other's state."""
    async with _session_lock:
        downgraded = session.update_from_heartbeat(data)
        session.save()
    return downgraded


async def safe_save(session: "LocalSession") -> None:
    """Lock around ``save()`` so two concurrent writes don't trample
    each other's partial state even though ``save()`` is itself atomic
    at the OS level — the in-memory mutations that precede it aren't."""
    async with _session_lock:
        session.save()