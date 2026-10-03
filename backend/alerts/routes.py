"""Alert API — channel / rule CRUD, alert history, manual retry.

All management endpoints require the ``manage_alerts`` permission
(or admin).  ``GET /api/alerts`` (history) is open to any
authenticated user so operators can see what's firing without
needing write access.

Permission gating follows the ``manage_api_keys`` pattern in
``app.py``.  The dependency ``require_manage_alerts`` raises 401
when unauthenticated and 403 when authenticated-but-unprivileged.
"""

import datetime
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import database, models, schemas
from .. import auth


router = APIRouter()


# --- Permission helper ------------------------------------------------------

def get_db():
    yield from database.get_db()


def _user_perms(user: models.User):
    if user.is_admin:
        return auth.ADMIN_PERMISSIONS
    return auth.parse_permissions(user)


def _require_manage_alerts(
    user: models.User = Depends(auth.get_auth_user),
    db: Session = Depends(get_db),
):
    """Dependency: require authenticated user with ``manage_alerts``.

    Admins auto-have it via ``ADMIN_PERMISSIONS``.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    perms = _user_perms(user)
    if "manage_alerts" not in perms:
        raise HTTPException(
            status_code=403, detail="Missing required permission: manage_alerts"
        )
    return user


def _record_audit(row, user: models.User) -> None:
    """Stamp the row with the acting user (user_id + username snapshot)."""
    now = datetime.datetime.now(datetime.UTC)
    if hasattr(row, "created_by_user_id") and getattr(row, "created_by_user_id", None) is None:
        row.created_by_user_id = user.id
        row.created_by_username = user.username
    row.last_edited_at = now
    row.last_edited_by_user_id = user.id
    row.last_edited_by_username = user.username


# --- Trigger / condition validation ----------------------------------------

VALID_TRIGGER_TYPES = {
    "space_occupied", "space_vacated", "space_edge",
    "lot_full_above_pct", "lot_open_below_pct",
    "camera_offline",
}

PER_SPACE_TRIGGERS = {"space_occupied", "space_vacated", "space_edge"}
THRESHOLD_TRIGGERS = {"lot_full_above_pct", "lot_open_below_pct"}


def _validate_condition(trigger_type: str, condition: dict) -> None:
    """Raise ValueError if the condition doesn't match its trigger_type."""
    if trigger_type not in VALID_TRIGGER_TYPES:
        raise ValueError(f"unknown trigger_type: {trigger_type!r}")
    if trigger_type in PER_SPACE_TRIGGERS:
        space_ids = condition.get("space_ids")
        if not isinstance(space_ids, list) or not space_ids or not all(
            isinstance(s, int) for s in space_ids
        ):
            raise ValueError(
                f"{trigger_type}: condition.space_ids must be a non-empty list of ints"
            )
    elif trigger_type in THRESHOLD_TRIGGERS:
        has_group = "camera_group_id" in condition
        has_camera = "camera_id" in condition
        if has_group == has_camera:  # both or neither
            raise ValueError(
                f"{trigger_type}: condition must have exactly one of "
                f"camera_group_id or camera_id"
            )
        fire = condition.get("fire_at_pct")
        resolve = condition.get("resolve_at_pct")
        if not isinstance(fire, (int, float)) or not isinstance(resolve, (int, float)):
            raise ValueError(
                f"{trigger_type}: condition.fire_at_pct and resolve_at_pct required (numeric)"
            )
        if trigger_type == "lot_full_above_pct" and not resolve < fire:
            raise ValueError(
                "lot_full_above_pct: resolve_at_pct must be strictly less than fire_at_pct"
            )
        if trigger_type == "lot_open_below_pct" and not resolve > fire:
            raise ValueError(
                "lot_open_below_pct: resolve_at_pct must be strictly greater than fire_at_pct"
            )
    elif trigger_type == "camera_offline":
        minutes = condition.get("minutes_offline")
        if not isinstance(minutes, (int, float)) or minutes <= 0:
            raise ValueError("camera_offline: condition.minutes_offline must be > 0")
        # camera_id is optional (None means "any camera")


def _validate_channel_config(channel_type: str, config: dict) -> None:
    if channel_type not in {"webhook", "email", "mqtt"}:
        raise ValueError(f"unknown channel_type: {channel_type!r}")
    if channel_type == "webhook":
        if not config.get("url"):
            raise ValueError("webhook channel requires config.url")
    elif channel_type == "email":
        to = config.get("to")
        if not isinstance(to, list) or not to or not all(isinstance(s, str) for s in to):
            raise ValueError("email channel requires config.to as non-empty list of strings")
    elif channel_type == "mqtt":
        # Phase E placeholder comment was never replaced when the
        # MQTT handler landed.  Without this validation the user
        # can save a channel with no broker or no topic and only
        # find out at dispatch time.  See channels/mqtt.py for
        # the full config shape.
        if not config.get("broker"):
            raise ValueError("mqtt channel requires config.broker")
        if not config.get("topic"):
            raise ValueError("mqtt channel requires config.topic")


def _validate_time_window(tw) -> None:
    if tw is None:
        return
    if not isinstance(tw, dict):
        raise ValueError("time_window must be a dict or null")
    if "days" in tw and tw["days"] is not None:
        if not isinstance(tw["days"], list) or not all(
            isinstance(d, int) and 0 <= d <= 6 for d in tw["days"]
        ):
            raise ValueError("time_window.days must be list of ints 0-6")
    if "start_hour" in tw and not isinstance(tw["start_hour"], int):
        raise ValueError("time_window.start_hour must be int")
    if "end_hour" in tw and not isinstance(tw["end_hour"], int):
        raise ValueError("time_window.end_hour must be int")


# --- CameraGroup CRUD -------------------------------------------------------

@router.get("/api/camera-groups", response_model=List[schemas.CameraGroupOut])
def list_camera_groups(
    user: models.User = Depends(auth.get_auth_user),
    db: Session = Depends(get_db),
):
    """List all camera groups with their camera_ids expanded."""
    groups = db.query(models.CameraGroup).order_by(models.CameraGroup.id.asc()).all()
    out = []
    for g in groups:
        ids = [
            m.camera_id
            for m in db.query(models.CameraGroupMembership)
            .filter_by(camera_group_id=g.id).all()
        ]
        item = schemas.CameraGroupOut(
            id=g.id, name=g.name, camera_ids=ids,
            created_at=g.created_at, last_edited_at=g.last_edited_at,
            created_by_user_id=g.created_by_user_id,
            created_by_username=g.created_by_username,
            last_edited_by_user_id=g.last_edited_by_user_id,
            last_edited_by_username=g.last_edited_by_username,
        )
        out.append(item)
    return out


@router.post(
    "/api/camera-groups",
    response_model=schemas.CameraGroupOut,
    status_code=status.HTTP_201_CREATED,
)
def create_camera_group(
    payload: schemas.CameraGroupCreate,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    if not payload.name:
        raise HTTPException(status_code=400, detail="name required")
    for cid in payload.camera_ids:
        if not db.query(models.Camera).filter_by(id=cid).first():
            raise HTTPException(
                status_code=400, detail=f"camera_id {cid} not found"
            )
    row = models.CameraGroup(name=payload.name)
    _record_audit(row, user)
    db.add(row)
    db.flush()
    # SQLite reuses rowids for INTEGER PRIMARY KEY without AUTOINCREMENT,
    # and the model's ON DELETE CASCADE on camera_group_memberships is
    # not actually enforced (PRAGMA foreign_keys=ON is never set on the
    # connection). That combination can leave orphaned (group_id,
    # camera_id) rows behind when a group is deleted — the next group
    # that gets the same id then collides on the composite PK. Clear
    # any stragglers for this id before inserting.
    db.query(models.CameraGroupMembership).filter_by(
        camera_group_id=row.id
    ).delete(synchronize_session=False)
    for cid in payload.camera_ids:
        db.add(models.CameraGroupMembership(
            camera_group_id=row.id, camera_id=cid,
        ))
    db.commit()
    db.refresh(row)
    ids = [m.camera_id for m in
           db.query(models.CameraGroupMembership).filter_by(camera_group_id=row.id).all()]
    return schemas.CameraGroupOut(
        id=row.id, name=row.name, camera_ids=ids,
        created_at=row.created_at, last_edited_at=row.last_edited_at,
        created_by_user_id=row.created_by_user_id,
        created_by_username=row.created_by_username,
        last_edited_by_user_id=row.last_edited_by_user_id,
        last_edited_by_username=row.last_edited_by_username,
    )


@router.put("/api/camera-groups/{group_id}", response_model=schemas.CameraGroupOut)
def update_camera_group(
    group_id: int,
    payload: schemas.CameraGroupUpdate,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    row = db.query(models.CameraGroup).filter_by(id=group_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Camera group not found")
    if payload.name is not None:
        if not payload.name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        row.name = payload.name
    if payload.camera_ids is not None:
        for cid in payload.camera_ids:
            if not db.query(models.Camera).filter_by(id=cid).first():
                raise HTTPException(
                    status_code=400, detail=f"camera_id {cid} not found"
                )
        # Replace membership wholesale.
        db.query(models.CameraGroupMembership).filter_by(
            camera_group_id=row.id
        ).delete()
        for cid in payload.camera_ids:
            db.add(models.CameraGroupMembership(
                camera_group_id=row.id, camera_id=cid,
            ))
    _record_audit(row, user)
    db.commit()
    db.refresh(row)
    ids = [m.camera_id for m in
           db.query(models.CameraGroupMembership).filter_by(camera_group_id=row.id).all()]
    return schemas.CameraGroupOut(
        id=row.id, name=row.name, camera_ids=ids,
        created_at=row.created_at, last_edited_at=row.last_edited_at,
        created_by_user_id=row.created_by_user_id,
        created_by_username=row.created_by_username,
        last_edited_by_user_id=row.last_edited_by_user_id,
        last_edited_by_username=row.last_edited_by_username,
    )


@router.delete("/api/camera-groups/{group_id}")
def delete_camera_group(
    group_id: int,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    row = db.query(models.CameraGroup).filter_by(id=group_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Camera group not found")
    # Fail-with-error if any rule references this group.
    rules = (
        db.query(models.AlertRule)
        .filter(models.AlertRule.trigger_type.in_(["lot_full_above_pct", "lot_open_below_pct"]))
        .all()
    )
    for r in rules:
        try:
            cond = json.loads(r.condition or "{}")
        except (TypeError, ValueError):
            continue
        if cond.get("camera_group_id") == group_id:
            raise HTTPException(
                status_code=409,
                detail=f"Camera group is referenced by rule {r.id} ({r.name}); "
                       f"reassign or delete that rule first",
            )
    # Memberships cascade via FK ON DELETE CASCADE.
    db.delete(row)
    db.commit()
    return {"ok": True}


# --- AlertChannel CRUD ------------------------------------------------------

@router.get("/api/alert-channels", response_model=List[schemas.AlertChannelOut])
def list_channels(
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    return db.query(models.AlertChannel).order_by(models.AlertChannel.id.asc()).all()


@router.post(
    "/api/alert-channels",
    response_model=schemas.AlertChannelOut,
    status_code=status.HTTP_201_CREATED,
)
def create_channel(
    payload: schemas.AlertChannelCreate,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    try:
        _validate_channel_config(payload.channel_type, payload.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    row = models.AlertChannel(
        name=payload.name,
        channel_type=payload.channel_type,
        config=json.dumps(payload.config or {}),
        enabled=payload.enabled,
    )
    _record_audit(row, user)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/api/alert-channels/{channel_id}", response_model=schemas.AlertChannelOut)
def update_channel(
    channel_id: int,
    payload: schemas.AlertChannelUpdate,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    row = db.query(models.AlertChannel).filter_by(id=channel_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")
    if payload.name is not None:
        row.name = payload.name
    if payload.channel_type is not None:
        row.channel_type = payload.channel_type
    if payload.config is not None:
        row.config = json.dumps(payload.config)
    if payload.enabled is not None:
        row.enabled = payload.enabled
    # Re-validate the resulting config
    try:
        cfg = json.loads(row.config or "{}")
        _validate_channel_config(row.channel_type, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _record_audit(row, user)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/api/alert-channels/{channel_id}")
def delete_channel(
    channel_id: int,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    row = db.query(models.AlertChannel).filter_by(id=channel_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")
    # Fail-with-error if any rule references this channel.
    ref = db.query(models.AlertRule).filter_by(channel_id=channel_id).first()
    if ref is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Channel is referenced by rule {ref.id} ({ref.name}); "
                   f"reassign or delete that rule first",
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


# --- AlertRule CRUD ---------------------------------------------------------

@router.get("/api/alert-rules", response_model=List[schemas.AlertRuleOut])
def list_rules(
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    return db.query(models.AlertRule).order_by(models.AlertRule.id.asc()).all()


@router.post(
    "/api/alert-rules",
    response_model=schemas.AlertRuleOut,
    status_code=status.HTTP_201_CREATED,
)
def create_rule(
    payload: schemas.AlertRuleCreate,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    try:
        _validate_condition(payload.trigger_type, payload.condition or {})
        _validate_time_window(payload.time_window)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if payload.channel_id is not None:
        ch = db.query(models.AlertChannel).filter_by(id=payload.channel_id).first()
        if ch is None:
            raise HTTPException(status_code=400, detail=f"channel_id {payload.channel_id} not found")
    row = models.AlertRule(
        name=payload.name,
        trigger_type=payload.trigger_type,
        condition=json.dumps(payload.condition or {}),
        channel_id=payload.channel_id,
        enabled=payload.enabled,
        cooldown_seconds=payload.cooldown_seconds,
        consecutive_count=payload.consecutive_count,
        time_window=json.dumps(payload.time_window) if payload.time_window else None,
    )
    _record_audit(row, user)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/api/alert-rules/{rule_id}", response_model=schemas.AlertRuleOut)
def update_rule(
    rule_id: int,
    payload: schemas.AlertRuleUpdate,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    row = db.query(models.AlertRule).filter_by(id=rule_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")
    if payload.name is not None:
        row.name = payload.name
    if payload.trigger_type is not None:
        row.trigger_type = payload.trigger_type
    if payload.condition is not None:
        row.condition = json.dumps(payload.condition)
    if payload.channel_id is not None:
        if payload.channel_id == 0:
            row.channel_id = None
        else:
            ch = db.query(models.AlertChannel).filter_by(id=payload.channel_id).first()
            if ch is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"channel_id {payload.channel_id} not found",
                )
            row.channel_id = payload.channel_id
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.cooldown_seconds is not None:
        row.cooldown_seconds = payload.cooldown_seconds
    if payload.consecutive_count is not None:
        row.consecutive_count = payload.consecutive_count
    if payload.time_window is not None:
        row.time_window = json.dumps(payload.time_window) if payload.time_window else None
    # Re-validate after applying the patch.
    try:
        cond = json.loads(row.condition or "{}")
        _validate_condition(row.trigger_type, cond)
        _validate_time_window(json.loads(row.time_window) if row.time_window else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _record_audit(row, user)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/api/alert-rules/{rule_id}")
def delete_rule(
    rule_id: int,
    force: bool = Query(False, description="Cascade-delete fired events for this rule"),
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    row = db.query(models.AlertRule).filter_by(id=rule_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")
    fired = db.query(models.AlertEvent).filter_by(rule_id=rule_id).first()
    if fired is not None and not force:
        raise HTTPException(
            status_code=409,
            detail=f"Rule has fired events; pass ?force=true to cascade-delete, "
                   f"or disable the rule instead",
        )
    # Cascade-delete events and runtime state (FK ON DELETE CASCADE handles it,
    # but be explicit so the test pin is unambiguous).
    db.query(models.AlertEvent).filter_by(rule_id=rule_id).delete()
    db.query(models.AlertState).filter_by(rule_id=rule_id).delete()
    db.query(models.AlertStatePerSpace).filter_by(rule_id=rule_id).delete()
    db.query(models.AlertStatePerCamera).filter_by(rule_id=rule_id).delete()
    db.delete(row)
    db.commit()
    return {"ok": True}


# --- Test channel -----------------------------------------------------------
# Sends a synthetic payload through the channel's delivery path so the
# operator can verify the URL / broker / SMTP is reachable before
# relying on it for real alerts.  Does NOT touch alert_events; this
# is a one-off probe that returns immediately.

_TEST_PAYLOAD = {
    "event_id": 0,
    "rule_id": 0,
    "rule_name": "TEST",
    "trigger_type": "test",
    "fired_at": "1970-01-01T00:00:00+00:00",
    "data": {"_test": True, "message": "This is a test alert from Park VIS."},
}


async def _run_channel_probe(channel_type: str, config: dict, channel_name: str = "(unsaved)"):
    """Shared body for the two test endpoints.

    Returns the result dict the API will serialize, or raises
    HTTPException for client-side errors (bad channel type).
    """
    import time
    from . import dispatcher as _dispatcher
    handler = _dispatcher.CHANNEL_DISPATCH.get(channel_type)
    if handler is None:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported channel_type: {channel_type!r}",
        )
    payload = dict(_TEST_PAYLOAD)
    payload["fired_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    payload["data"] = dict(_TEST_PAYLOAD["data"])
    payload["data"]["channel_name"] = channel_name

    start = time.monotonic()
    try:
        # ``loop`` was previously assigned here but never used — left
        # over from an earlier version that ran the handler via
        # ``loop.run_until_complete``. ``await handler(...)`` works
        # directly because these endpoints are already async.
        result = await handler(config, payload)
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": False,
            "error": f"dispatcher exception: {exc!s}",
            "latency_ms": round(latency_ms, 1),
        }
    latency_ms = (time.monotonic() - start) * 1000
    if hasattr(result, "success"):
        return {
            "success": bool(result.success),
            "status_code": getattr(result, "status_code", None),
            "error": getattr(result, "error", None),
            "latency_ms": round(latency_ms, 1),
        }
    return {
        "success": False,
        "error": "unknown result from channel handler",
        "latency_ms": round(latency_ms, 1),
    }


@router.post("/api/alert-channels/test-config")
async def test_channel_config(
    payload: dict,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    """Probe a channel config *before* saving it.

    Accepts the same shape as a channel create/update
    (``{name, channel_type, config, enabled}``) and runs a probe
    through the channel handler.  Does NOT persist anything.  This
    is what the form's "Test" button uses when the operator is
    configuring a new channel.
    """
    channel_type = payload.get("channel_type")
    config = payload.get("config") or {}
    if not channel_type:
        raise HTTPException(status_code=400, detail="channel_type required")
    name = payload.get("name") or "(unsaved)"
    return await _run_channel_probe(channel_type, config, channel_name=name)


@router.post("/api/alert-channels/{channel_id}/test")
async def test_channel(
    channel_id: int,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    """Send a synthetic payload through the channel and report the result.

    The response shape mirrors the channel's ``WebhookResult`` (or its
    email/mqtt equivalents) so the UI can show the operator whether
    the destination accepted the probe.  On success: ``{"success": True,
    "status_code": 200}``.  On failure: ``{"success": False,
    "error": "..."}``.
    """
    channel = db.query(models.AlertChannel).filter_by(id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    if not channel.enabled:
        raise HTTPException(status_code=400, detail="Channel is disabled")
    try:
        config = json.loads(channel.config or "{}")
    except (TypeError, ValueError):
        config = {}
    return await _run_channel_probe(
        channel.channel_type, config, channel_name=channel.name,
    )


# --- Test fire ---------------------------------------------------------------
# Synthesizes an ``alert_event`` row for a rule (without driving the
# real engine state machine) and runs it through the dispatcher.  This
# is the "is the channel wired up correctly?" check, separate from
# "does the rule fire on real data?".  The synthesized event is
# marked with ``payload._test = True`` so the operator can tell.

@router.post("/api/alert-rules/{rule_id}/test-fire")
async def test_fire_rule(
    rule_id: int,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    """Create a synthetic alert_event for the rule and dispatch it.

    The event goes through the same channel path as a real fire, so
    the operator can verify the full delivery chain.  The synthesized
    payload includes ``_test: True`` and the rule's name.  Returns
    the delivery result (success / failure / error).
    """
    import time
    from . import dispatcher as _dispatcher
    rule = db.query(models.AlertRule).filter_by(id=rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if not rule.enabled:
        raise HTTPException(status_code=400, detail="Rule is disabled")
    if not rule.channel_id:
        raise HTTPException(status_code=400, detail="Rule has no channel")
    channel = db.query(models.AlertChannel).filter_by(id=rule.channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    if not channel.enabled:
        raise HTTPException(status_code=400, detail="Channel is disabled")

    now = datetime.datetime.now(datetime.UTC)
    # Build a payload that matches the engine's real-fire shape so
    # operators can verify receivers correctly parse the rich fields
    # (camera/space names + IDs, current_pct, etc.).  The ``_test``
    # flag tells the receiver (and the history view) that this is a
    # probe, not a real alert.
    try:
        cond = json.loads(rule.condition or "{}")
    except (TypeError, ValueError):
        cond = {}
    payload_dict = {
        "trigger_type": rule.trigger_type,
        "_test": True,
        "message": f"Test fire for rule {rule.name!r}",
    }
    if rule.trigger_type in ("space_occupied", "space_vacated", "space_edge"):
        # Pull space + camera names for the first space in the rule's
        # condition so the operator sees a meaningful summary, not
        # just IDs.
        space_ids = cond.get("space_ids") or []
        if space_ids:
            sp = db.query(models.Space).filter_by(id=space_ids[0]).first()
            payload_dict["space_id"] = sp.id if sp else space_ids[0]
            payload_dict["space_name"] = sp.name if sp else None
            payload_dict["space_state"] = (
                "occupied" if rule.trigger_type == "space_occupied"
                else "vacant" if rule.trigger_type == "space_vacated"
                else "edge_change"
            )
            if sp is not None:
                cam = db.query(models.Camera).filter_by(id=sp.camera_id).first()
                payload_dict["camera_id"] = sp.camera_id
                payload_dict["camera_name"] = cam.name if cam else None
        payload_dict["edge"] = (
            "became_occupied" if rule.trigger_type == "space_occupied"
            else "became_vacant" if rule.trigger_type == "space_vacated"
            else "edge_change"
        )
    elif rule.trigger_type in ("lot_full_above_pct", "lot_open_below_pct"):
        # For threshold rules, the receiver can see the configured
        # thresholds + scope.  We don't compute current_pct on a
        # test fire — that's a snapshot, not a real measurement.
        payload_dict["current_pct"] = cond.get("fire_at_pct")  # rough placeholder
        payload_dict["fire_at_pct"] = cond.get("fire_at_pct")
        payload_dict["resolve_at_pct"] = cond.get("resolve_at_pct")
        payload_dict["spaces_occupied"] = None
        payload_dict["spaces_vacant"] = None
        payload_dict["spaces_total"] = None
        group_id = cond.get("camera_group_id")
        single_camera_id = cond.get("camera_id")
        if group_id is not None:
            grp = db.query(models.CameraGroup).filter_by(id=group_id).first()
            payload_dict["camera_group_id"] = group_id
            payload_dict["camera_group_name"] = grp.name if grp else None
        elif single_camera_id is not None:
            cam = db.query(models.Camera).filter_by(id=single_camera_id).first()
            payload_dict["camera_id"] = single_camera_id
            payload_dict["camera_name"] = cam.name if cam else None
    elif rule.trigger_type == "camera_offline":
        payload_dict["camera_id"] = cond.get("camera_id")
        payload_dict["minutes_offline"] = cond.get("minutes_offline")
        if cond.get("camera_id") is not None:
            cam = db.query(models.Camera).filter_by(id=cond["camera_id"]).first()
            payload_dict["camera_name"] = cam.name if cam else None
        else:
            payload_dict["camera_name"] = "(any camera)"
    event = models.AlertEvent(
        rule_id=rule.id, fired_at=now, status="fired",
        payload=json.dumps(payload_dict), attempts=0,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Hand off to the dispatcher's per-event delivery path.  We do
    # NOT use the background dispatcher's poll loop — the test
    # response should be immediate.
    try:
        config = json.loads(channel.config or "{}")
    except (TypeError, ValueError):
        config = {}

    envelope = {
        "event_id": event.id,
        "rule_id": rule.id,
        "rule_name": rule.name,
        "trigger_type": rule.trigger_type,
        "fired_at": event.fired_at.isoformat() if event.fired_at else None,
        "data": payload_dict,
    }

    handler = _dispatcher.CHANNEL_DISPATCH.get(channel.channel_type)
    start = time.monotonic()
    if handler is None:
        event.status = "failed"
        event.last_error = f"unsupported channel_type: {channel.channel_type}"
        event.attempts = 1
        event.last_attempt_at = now
        db.commit()
        return {
            "success": False,
            "error": event.last_error,
            "event_id": event.id,
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
        }
    try:
        # See note in test_channel — ``loop`` was unused dead code.
        result = await handler(config, envelope)
    except Exception as exc:  # noqa: BLE001
        event.status = "failed"
        event.last_error = f"dispatcher exception: {exc!s}"
        event.attempts = 1
        event.last_attempt_at = now
        db.commit()
        return {
            "success": False,
            "error": event.last_error,
            "event_id": event.id,
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
        }
    event.attempts = 1
    event.last_attempt_at = now
    if result.success:
        event.status = "sent"
        event.delivered_at = now
        event.last_error = None
    else:
        event.status = "failed"
        event.last_error = result.error or "unknown error"
    db.commit()
    return {
        "success": bool(result.success),
        "status_code": getattr(result, "status_code", None),
        "error": getattr(result, "error", None),
        "event_id": event.id,
        "latency_ms": round((time.monotonic() - start) * 1000, 1),
    }


# --- Alert history ----------------------------------------------------------

@router.get("/api/alerts", response_model=List[schemas.AlertEventOut])
def list_alerts(
    status_filter: Optional[str] = Query(None, alias="status"),
    rule_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    since: Optional[datetime.datetime] = None,
    until: Optional[datetime.datetime] = None,
    limit: int = Query(100, ge=1, le=1000),
    user: models.User = Depends(auth.get_auth_user),  # any authenticated user
    db: Session = Depends(get_db),
):
    """List alert history.  Read-only; any authenticated user can see it.

    Operators without ``manage_alerts`` see the list but not the
    retry/manage endpoints.  Management UI hides the action buttons
    based on permission.
    """
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    q = db.query(models.AlertEvent)
    if status_filter is not None:
        q = q.filter(models.AlertEvent.status == status_filter)
    if rule_id is not None:
        q = q.filter(models.AlertEvent.rule_id == rule_id)
    if channel_id is not None:
        q = q.filter(models.AlertRule.channel_id == channel_id).join(models.AlertRule)
    if since is not None:
        q = q.filter(models.AlertEvent.fired_at >= since)
    if until is not None:
        q = q.filter(models.AlertEvent.fired_at <= until)
    q = q.order_by(models.AlertEvent.fired_at.desc()).limit(limit)
    return q.all()


@router.post("/api/alerts/{event_id}/retry")
def retry_alert(
    event_id: int,
    user: models.User = Depends(_require_manage_alerts),
    db: Session = Depends(get_db),
):
    """Manually retry a failed alert.

    Resets status to 'fired' so the dispatcher picks it up on the
    next tick.  Refuses (409) if the event has already exhausted its
    retry budget (attempts >= MAX_ATTEMPTS) — the operator can still
    create a new event by re-arming the rule, but a single event
    can only be attempted a fixed number of times.
    """
    from . import dispatcher as _dispatcher
    event = db.query(models.AlertEvent).filter_by(id=event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Alert event not found")
    if event.status != "failed":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry event in status {event.status!r}; "
                   f"only 'failed' events are retryable.  Successfully "
                   f"delivered events cannot be re-fired (use the rule's "
                   f"test-fire endpoint to verify the channel instead).",
        )
    if event.attempts >= _dispatcher.MAX_ATTEMPTS:
        raise HTTPException(
            status_code=409,
            detail=f"Event has already attempted {_dispatcher.MAX_ATTEMPTS} times; "
                   f"re-arm the rule to fire a new event",
        )
    event.status = "fired"
    event.last_error = None
    event.next_retry_at = None
    # Note: we don't reset attempts — the manual retry counts toward
    # the total.  This prevents an operator from looping manual
    # retries indefinitely to bypass the cap.
    db.commit()
    return {"ok": True, "id": event.id, "status": event.status}
