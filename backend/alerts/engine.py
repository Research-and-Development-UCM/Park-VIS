"""Alert engine — tails ``Occupancy`` rows, evaluates rules, fires alerts.

Phase B scope (per-space edge rules + camera_offline baseline):

  - Tail ``Occupancy`` rows.  For each new row, look up the space's
    camera and any enabled rules whose ``condition.space_ids``
    includes this space.  Drive the hysteresis state machine; on
    ``fire``, write an ``alert_events`` row with ``status='fired'``
    and update the runtime state in ``alert_state_per_space``.

  - Camera-offline rule: a separate poller that checks each enabled
    camera's last ``CameraScan`` recency.  This is wired up here but
    the camera-offline rule is only enabled starting in phase D; the
    poller is the harness.

  - Threshold rules (lot-full / lot-open) land in phase C.

The engine is started by ``backend.alerts.engine.start_background``,
which is called from ``main.py``'s lifespan — same pattern as the
scheduler.
"""

import asyncio
import datetime
import json
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import database, models
from .hysteresis import (
    SpaceRuleState,
    ThresholdRuleState,
    cooldown_elapsed,
    evaluate_space_rule,
    evaluate_threshold_rule,
    within_time_window,
)
from ..logging_config import vulture_logger as logger


ENGINE_TICK_SECONDS = 5.0       # how often we poll for new Occupancy rows

_engine_task: Optional[asyncio.Task] = None
_engine_stop = asyncio.Event()
# Sentinel: -1 means "never hydrated".  The first tick will hydrate to
# max(occupancy.id) so we don't re-process historical rows on startup.
# Tests set this to a specific value to control which rows are seen.
_last_seen_occupancy_id: int = -1


# --- Per-space rule evaluation ----------------------------------------------

def _space_rule_matches_space(rule: models.AlertRule, space_id: int) -> bool:
    """Return True if this per-space rule includes ``space_id``."""
    try:
        cond = json.loads(rule.condition or "{}")
    except (TypeError, ValueError):
        return False
    space_ids = cond.get("space_ids") or []
    return space_id in space_ids


def _load_per_space_state(db: Session, rule_id: int, space_id: int) -> SpaceRuleState:
    row = (
        db.query(models.AlertStatePerSpace)
        .filter_by(rule_id=rule_id, space_id=space_id)
        .first()
    )
    if row is None:
        return SpaceRuleState()
    return SpaceRuleState(
        state=row.state or "armed",
        consecutive_count_current=row.consecutive_count_current or 0,
        last_observed_occupied=row.last_observed_occupied,
        firing_target=row.firing_target,
    )


def _save_per_space_state(
    db: Session, rule_id: int, space_id: int, st: SpaceRuleState
) -> None:
    row = (
        db.query(models.AlertStatePerSpace)
        .filter_by(rule_id=rule_id, space_id=space_id)
        .first()
    )
    if row is None:
        row = models.AlertStatePerSpace(
            rule_id=rule_id, space_id=space_id, state=st.state,
            consecutive_count_current=st.consecutive_count_current,
            last_observed_occupied=st.last_observed_occupied,
            firing_target=st.firing_target,
        )
        db.add(row)
    else:
        row.state = st.state
        row.consecutive_count_current = st.consecutive_count_current
        row.last_observed_occupied = st.last_observed_occupied
        row.firing_target = st.firing_target


def _load_rule_state(db: Session, rule_id: int) -> models.AlertState:
    row = db.query(models.AlertState).filter_by(rule_id=rule_id).first()
    if row is None:
        row = models.AlertState(rule_id=rule_id)
        db.add(row)
        db.flush()
    return row


def _should_fire_after_cooldown(rule: models.AlertRule, db: Session) -> bool:
    """Return True if the rule's cooldown has elapsed since its last fire.

    Note: ``rule.cooldown_seconds or 180`` would silently turn a
    configured value of 0 into 180, since 0 is falsy in Python.  We
    use an explicit None check instead.
    """
    state = _load_rule_state(db, rule.id)
    last_iso = state.last_fired_at.isoformat() if state.last_fired_at else None
    cooldown = rule.cooldown_seconds if rule.cooldown_seconds is not None else 180
    return cooldown_elapsed(last_iso, datetime.datetime.now(datetime.UTC).isoformat(),
                            cooldown)


def _record_fire(db: Session, rule: models.AlertRule,
                 payload_dict: dict) -> models.AlertEvent:
    """Write an alert_events row and update the rule's runtime state.

    ``payload_dict`` is the engine-built context dict (edge direction,
    space/camera names + IDs, etc.) — the dispatcher wraps it in the
    final envelope so the channel handler doesn't need to know
    trigger-type internals.  The DB stores it verbatim as JSON text.
    """
    now = datetime.datetime.now(datetime.UTC)
    event = models.AlertEvent(
        rule_id=rule.id, fired_at=now, status="fired",
        payload=json.dumps(payload_dict),
        attempts=0,
    )
    db.add(event)
    db.flush()  # populate event.id
    state = _load_rule_state(db, rule.id)
    state.last_fired_at = now
    state.last_fired_event_id = event.id
    return event


def _build_per_space_payload(
    db: Session, rule: models.AlertRule, occ: models.Occupancy
) -> dict:
    """Build the rich payload for a per-space rule fire.

    Includes edge direction (the space's new state), space name + ID,
    and the camera name + ID that owns the space.  The receiver
    (webhook/email/mqtt) can render a human-readable summary without
    needing to query the database.
    """
    space = db.query(models.Space).filter_by(id=occ.space_id).first()
    cam = (
        db.query(models.Camera).filter_by(id=space.camera_id).first()
        if space is not None else None
    )
    new_state = "occupied" if bool(occ.occupied) else "vacant"
    # Human-friendly edge description:
    #   space_occupied -> "became occupied"
    #   space_vacated  -> "became vacant"
    #   space_edge     -> "edge observed" + new state
    if rule.trigger_type == "space_occupied":
        edge = "became_occupied"
    elif rule.trigger_type == "space_vacated":
        edge = "became_vacant"
    else:  # space_edge
        edge = "edge_change"
    return {
        "trigger_type": rule.trigger_type,
        "edge": edge,
        "space_state": new_state,
        "space_id": occ.space_id,
        "space_name": space.name if space else None,
        "camera_id": space.camera_id if space else None,
        "camera_name": cam.name if cam else None,
    }


def _process_occupancy_row(
    db: Session, occ: models.Occupancy, rules: list
) -> None:
    """For a single Occupancy row, evaluate every matching per-space rule.

    ``rules`` is the pre-fetched list of enabled per-space rules
    (already filtered by trigger_type and enabled flag).  Fetching
    once per tick instead of once per Occupancy row avoids the N+1
    pattern the engine used to have — at 100 spaces per tick that
    was 100 redundant rule queries.
    """
    # The time-window check below used to read datetime.now(UTC), which
    # is wrong during backlog replay: if the engine was paused and a
    # batch of old Occupancy rows arrives, every row's window check
    # runs against the same "now" instead of the time the event
    # actually happened. Use the row's scan timestamp so a rule with
    # a 9-5 window fires for 9-5 events and not for backfill at 3am.
    scan = db.query(models.CameraScan).filter_by(id=occ.scan_id).first()
    scan_iso = scan.timestamp.isoformat() if scan else datetime.datetime.now(datetime.UTC).isoformat()

    for rule in rules:
        if not _space_rule_matches_space(rule, occ.space_id):
            continue
        try:
            tw = json.loads(rule.time_window) if rule.time_window else None
        except (TypeError, ValueError):
            tw = None
        if not within_time_window(scan_iso, tw):
            continue

        prev = _load_per_space_state(db, rule.id, occ.space_id)
        result = evaluate_space_rule(
            prev, observed_occupied=bool(occ.occupied),
            trigger_type=rule.trigger_type,
            consecutive_count=(
                rule.consecutive_count if rule.consecutive_count is not None else 1
            ),
        )

        if result.fire:
            # Cooldown is per-rule (not per-space), so check the rule's
            # state row, not the per-space one.
            if not _should_fire_after_cooldown(rule, db):
                # Don't save the post-evaluation state — the state
                # machine advanced to "firing" but the fire was
                # blocked, so saving would strand us in "firing"
                # and the next tick wouldn't see a transition
                # (the state machine only fires on armed/buffering
                # -> firing).  Discard the result and keep the
                # previous state so the next observation re-evaluates.
                continue
            # Cooldown elapsed: persist the new state and record the
            # fire.
            _save_per_space_state(db, rule.id, occ.space_id, result.new_state)
            payload_dict = _build_per_space_payload(db, rule, occ)
            _record_fire(db, rule, payload_dict)
        else:
            # Non-fire observation: just persist the new state
            # (e.g. armed -> buffering, or firing -> armed on
            # re-arm).
            _save_per_space_state(db, rule.id, occ.space_id, result.new_state)


# --- Camera-offline rule evaluation ----------------------------------------

def _last_scan_for_camera(db: Session, camera_id: int):
    """Return the most recent CameraScan for the camera, or None."""
    return (
        db.query(models.CameraScan)
        .filter_by(camera_id=camera_id)
        .order_by(models.CameraScan.timestamp.desc())
        .first()
    )


def _process_camera_offline_rules(
    db: Session, now: datetime.datetime, cameras_by_id: dict
) -> None:
    """For each enabled camera_offline rule, check the target camera's
    last CameraScan.  Fire if the gap exceeds ``minutes_offline``.

    The condition can specify a single ``camera_id`` or omit it
    (``camera_id not in condition``) which means "any enabled
    camera".  Per-camera state is stored in
    ``alert_state_per_camera`` so the same rule doesn't re-fire for
    the same camera while it remains offline.

    ``cameras_by_id`` is the pre-fetched ``{id: Camera}`` map the
    engine loaded once at the top of the tick, so the per-camera
    lookup in the loop below is a dict hit instead of a redundant
    DB query.
    """
    rules = (
        db.query(models.AlertRule)
        .filter(
            models.AlertRule.enabled == True,  # noqa: E712
            models.AlertRule.trigger_type == "camera_offline",
        )
        .all()
    )
    for rule in rules:
        try:
            cond = json.loads(rule.condition or "{}")
        except (TypeError, ValueError):
            continue
        minutes = cond.get("minutes_offline")
        if not isinstance(minutes, (int, float)) or minutes <= 0:
            continue
        try:
            tw = json.loads(rule.time_window) if rule.time_window else None
        except (TypeError, ValueError):
            tw = None
        if not within_time_window(now.isoformat(), tw):
            continue

        target_camera_id = cond.get("camera_id")
        if target_camera_id is not None:
            target_cameras = [target_camera_id]
        else:
            # "any enabled camera" — use the pre-fetched map
            # filtered in-place instead of re-querying.
            target_cameras = [
                cid for cid, c in cameras_by_id.items()
                if c.is_enabled and not c.is_test
            ]

        threshold_seconds = float(minutes) * 60.0
        for cam_id in target_cameras:
            # Dict hit instead of a fresh DB query — the cameras
            # map was built once at the top of the tick.
            cam = cameras_by_id.get(cam_id)
            if cam is None:
                db.query(models.AlertStatePerCamera).filter_by(
                    rule_id=rule.id, camera_id=cam_id
                ).delete()
                continue

            last_scan = _last_scan_for_camera(db, cam_id)
            if last_scan is None:
                # No scan ever recorded.  Treat as offline since the
                # rule was created (so a fresh install doesn't fire
                # immediately for cameras that haven't scanned yet).
                reference = rule.created_at or now
            else:
                reference = last_scan.timestamp

            from datetime import timezone
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            gap = (now - reference).total_seconds()
            is_offline = gap > threshold_seconds

            state_row = (
                db.query(models.AlertStatePerCamera)
                .filter_by(rule_id=rule.id, camera_id=cam_id)
                .first()
            )
            currently_flagged = state_row is not None

            if is_offline and not currently_flagged:
                # H12 audit fix: do NOT create the state row in the
                # cooldown path.  Previously the code set the row
                # here and ``continue``d, which meant on the next
                # tick ``currently_flagged`` was True and the
                # ``is_offline and not currently_flagged`` branch
                # never ran again — the alert was suppressed for
                # the entire offline episode.  Now we just skip
                # the tick and let the next tick re-evaluate.
                if not _should_fire_after_cooldown(rule, db):
                    continue
                payload_dict = {
                    "trigger_type": "camera_offline",
                    "camera_id": cam_id,
                    "camera_name": cam.name,
                    "minutes_offline": minutes,
                    "last_scan_at": (
                        last_scan.timestamp.isoformat() if last_scan else None
                    ),
                    "gap_seconds": gap,
                }
                event = _record_fire(db, rule, payload_dict)
                # State row is set ONLY in the fire path now.
                if state_row is None:
                    db.add(models.AlertStatePerCamera(
                        rule_id=rule.id, camera_id=cam_id,
                        offline_since=reference,
                    ))
                else:
                    state_row.offline_since = reference
            elif not is_offline and currently_flagged:
                db.delete(state_row)


# --- Threshold rule evaluation ---------------------------------------------

def _group_camera_ids(db: Session, group_id: int) -> list:
    """Return the list of camera_ids in the group, or [] if the group is gone."""
    rows = (
        db.query(models.CameraGroupMembership)
        .filter_by(camera_group_id=group_id)
        .all()
    )
    return [r.camera_id for r in rows]


def _resolve_threshold_camera_ids(db: Session, condition: dict) -> Optional[list]:
    """Resolve a threshold rule's condition to a list of camera_ids.

    A threshold rule scopes to either a camera group OR a single
    camera (the GUI offers both; the DB stores whichever the user
    picked).  Returns the camera_ids to compute occupancy across, or
    None if the condition is misconfigured (neither / both set).

    The caller treats None as "skip this rule this tick" — the API
    validator should have rejected the bad condition on write, but
    we defend in depth here in case the row was hand-edited.
    """
    group_id = condition.get("camera_group_id")
    camera_id = condition.get("camera_id")
    has_group = group_id is not None
    has_camera = camera_id is not None
    if has_group and has_camera:
        return None
    if has_group:
        return _group_camera_ids(db, int(group_id))
    if has_camera:
        return [int(camera_id)]
    return None


def _compute_group_occupancy(db: Session, camera_ids: list) -> Optional[dict]:
    """Return the current occupancy stats for the scope, or None.

    The "current" occupancy is taken from the latest ``Occupancy`` row
    per space in any of the scope's cameras.  If a space has no row at
    all, it's counted as unknown and excluded from both the numerator
    and the denominator (we can't say it's occupied or vacant).  This
    is the safest default: a scope with no observations reports as
    0/total, not 0/0, and an alert condition on a fully-unknown scope
    never fires (because the rule has nothing to compare).

    Returns a dict with ``occupied``, ``vacant``, ``total`` counts and
    a ``pct`` (0-100), or ``None`` if the scope has no observed spaces
    yet.  ``pct`` is rounded to two decimals — the original
    ``_compute_group_occupancy_pct`` contract.
    """
    if not camera_ids:
        return None
    spaces = db.query(models.Space).filter(models.Space.camera_id.in_(camera_ids)).all()
    if not spaces:
        return None
    space_ids = [s.id for s in spaces]
    from sqlalchemy import select, func
    subq = select(func.max(models.Occupancy.id)).group_by(models.Occupancy.space_id)
    latest = (
        db.query(models.Occupancy)
        .filter(models.Occupancy.id.in_(subq))
        .filter(models.Occupancy.space_id.in_(space_ids))
        .all()
    )
    # Only count spaces that have at least one observation.
    observed = {occ.space_id: bool(occ.occupied) for occ in latest}
    if not observed:
        return None
    total = len(observed)
    occupied = sum(1 for v in observed.values() if v)
    vacant = total - occupied
    pct = (occupied / total) * 100.0
    return {
        "occupied": occupied,
        "vacant": vacant,
        "total": total,
        "pct": round(pct, 2),
    }


def _compute_group_occupancy_pct(db: Session, camera_ids: list) -> Optional[float]:
    """Return just the current occupancy % (kept for back-compat callers).

    The newer ``_compute_group_occupancy`` returns the full stats; the
    engine's threshold evaluator now uses that directly.  This thin
    wrapper exists so external callers (or test fixtures) that only
    need the % still work.
    """
    stats = _compute_group_occupancy(db, camera_ids)
    if stats is None:
        return None
    return stats["pct"]


def _process_threshold_rules(db: Session, now: datetime.datetime) -> None:
    """For each enabled threshold rule, compute the current occupancy
    % and drive the hysteresis state machine.  Fires on transition
    from armed to firing.
    """
    rules = (
        db.query(models.AlertRule)
        .filter(
            models.AlertRule.enabled == True,  # noqa: E712
            models.AlertRule.trigger_type.in_(
                ["lot_full_above_pct", "lot_open_below_pct"]
            ),
        )
        .all()
    )
    for rule in rules:
        try:
            cond = json.loads(rule.condition or "{}")
        except (TypeError, ValueError):
            continue
        fire_at = cond.get("fire_at_pct")
        resolve_at = cond.get("resolve_at_pct")
        if not isinstance(fire_at, (int, float)) \
                or not isinstance(resolve_at, (int, float)):
            continue
        # Time window gate (same as per-space rules).
        try:
            tw = json.loads(rule.time_window) if rule.time_window else None
        except (TypeError, ValueError):
            tw = None
        if not within_time_window(now.isoformat(), tw):
            continue

        # Scope: a camera_group_id OR a single camera_id.  Either
        # works — the helper resolves both to a list of camera_ids.
        camera_ids = _resolve_threshold_camera_ids(db, cond)
        if not camera_ids:
            # Misconfigured (both/neither), empty group, or deleted
            # scope.  Skip silently.
            continue
        stats = _compute_group_occupancy(db, camera_ids)
        if stats is None:
            # No spaces have any observations yet.
            continue
        current_pct = stats["pct"]

        state_row = _load_rule_state(db, rule.id)
        prev = ThresholdRuleState(
            state=state_row.state or "armed",
            last_observed_value=(
                float(state_row.last_observed_value)
                if state_row.last_observed_value
                else None
            ),
            last_fired_at=(
                state_row.last_fired_at.isoformat()
                if state_row.last_fired_at
                else None
            ),
        )
        try:
            result = evaluate_threshold_rule(
                prev,
                current_pct=current_pct,
                trigger_type=rule.trigger_type,
                fire_at_pct=float(fire_at),
                resolve_at_pct=float(resolve_at),
            )
        except ValueError:
            # Misconfigured rule (resolve on the wrong side of fire).
            # Skip silently — the API rejects these on create/update,
            # but a bad row in the DB shouldn't crash the engine.
            continue

        # Persist the observed value always.  For the state field,
        # the safe default is: save whatever the state machine
        # produced, EXCEPT for the armed -> firing transition when
        # cooldown blocks the fire.  In that one case, leave the
        # previous state in place so the next tick re-evaluates
        # instead of seeing "firing" with no fire and never
        # transitioning again.  The firing -> armed transition
        # (below resolve) and the armed -> firing transition with
        # cooldown elapsed are both safe to persist.
        state_row.last_observed_value = str(current_pct)

        if result.fire and result.new_state.state == "firing":
            # State machine wants to transition to "firing".  If
            # cooldown blocks, skip the state write — keep the
            # previous state so the next tick re-evaluates.
            if not _should_fire_after_cooldown(rule, db):
                continue

        state_row.state = result.new_state.state

        if result.fire:
            # Build the rich payload: trigger_type, threshold values,
            # current occupancy stats, and the scope (camera or camera
            # group) by name + ID so the receiver can render a
            # human-readable summary without re-querying the DB.
            scope_payload = {
                "trigger_type": rule.trigger_type,
                "current_pct": current_pct,
                "fire_at_pct": float(fire_at),
                "resolve_at_pct": float(resolve_at),
                "spaces_occupied": stats["occupied"],
                "spaces_vacant": stats["vacant"],
                "spaces_total": stats["total"],
            }
            group_id = cond.get("camera_group_id")
            single_camera_id = cond.get("camera_id")
            if group_id is not None:
                group = db.query(models.CameraGroup).filter_by(id=group_id).first()
                scope_payload["camera_group_id"] = group_id
                scope_payload["camera_group_name"] = group.name if group else None
            elif single_camera_id is not None:
                cam = db.query(models.Camera).filter_by(id=single_camera_id).first()
                scope_payload["camera_id"] = single_camera_id
                scope_payload["camera_name"] = cam.name if cam else None
            _record_fire(db, rule, scope_payload)


# --- Main loop --------------------------------------------------------------

def _tick() -> None:
    """One engine pass.  Process new Occupancy rows + camera-offline check."""
    db = database.SessionLocal()
    try:
        global _last_seen_occupancy_id
        # Hydrate on first tick.  -1 is the "never hydrated" sentinel;
        # any other value (including 0) was set explicitly by a caller
        # (typically a test) and must be respected.
        if _last_seen_occupancy_id == -1:
            max_id = db.query(func.max(models.Occupancy.id)).scalar()
            _last_seen_occupancy_id = int(max_id or 0)

        new_rows = (
            db.query(models.Occupancy)
            .filter(models.Occupancy.id > _last_seen_occupancy_id)
            .order_by(models.Occupancy.id.asc())
            .all()
        )
        # Pre-fetch the enabled per-space rules once per tick and
        # pass them in.  The old code re-queried the rules table
        # for every Occupancy row, which was a 100x N+1 at 100
        # spaces per tick.
        per_space_rules = (
            db.query(models.AlertRule)
            .filter(
                models.AlertRule.enabled == True,  # noqa: E712
                models.AlertRule.trigger_type.in_(
                    ["space_occupied", "space_vacated", "space_edge"]
                ),
            )
            .all()
        )
        # Pre-fetch all cameras once for the camera-offline loop.
        # The old code re-queried the cameras table for every
        # (rule, camera) pair; this is a single read that builds
        # the {id: Camera} map the loop indexes into.
        all_cameras = db.query(models.Camera).all()
        cameras_by_id = {c.id: c for c in all_cameras}

        for occ in new_rows:
            # Audit fix: process + commit each row independently.  A single
            # bad row previously raised out of the loop, which rolled back
            # every good row written before it AND (because the cursor had
            # already advanced past the failing id) skipped all rows after
            # it forever.  Committing per row keeps earlier work, and the
            # cursor advance in ``finally`` keeps a permanently-bad row from
            # wedging the engine while letting the rest proceed.
            try:
                _process_occupancy_row(db, occ, per_space_rules)
                db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.error("Alert engine failed to process occupancy {id}: {err}", id=occ.id, err=exc)
                db.rollback()
            finally:
                _last_seen_occupancy_id = max(_last_seen_occupancy_id, occ.id)

        # Threshold rules don't depend on the occupancy tail — they
        # compute the current group occupancy % from the latest row
        # per space.  Evaluate them on every tick.
        try:
            _process_threshold_rules(db, datetime.datetime.now(datetime.UTC))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("Alert engine threshold pass failed: {err}", err=exc)
            db.rollback()
        try:
            _process_camera_offline_rules(
                db, datetime.datetime.now(datetime.UTC), cameras_by_id
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("Alert engine camera-offline pass failed: {err}", err=exc)
            db.rollback()
    except Exception as exc:  # noqa: BLE001
        logger.error("Alert engine tick failed: {err}", err=exc)
        db.rollback()
    finally:
        db.close()


async def _engine_loop() -> None:
    logger.info("Alert engine started")
    while not _engine_stop.is_set():
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001
            logger.error("Alert engine loop error: {err}", err=exc)
        try:
            await asyncio.wait_for(
                _engine_stop.wait(), timeout=ENGINE_TICK_SECONDS
            )
        except asyncio.TimeoutError:
            pass
    logger.info("Alert engine stopped")


def start_background() -> None:
    """Start the engine task.  Idempotent."""
    global _engine_task, _engine_stop
    if _engine_task is not None and not _engine_task.done():
        return
    _engine_stop = asyncio.Event()
    _engine_task = asyncio.create_task(_engine_loop())



def stop_background() -> None:
    """Signal the engine task to exit."""
    global _engine_task
    _engine_stop.set()
    _engine_task = None


def reset_events() -> None:
    """Reset module-level singletons.  Used by tests."""
    global _engine_task, _engine_stop, _last_seen_occupancy_id
    if _engine_task is not None and not _engine_task.done():
        _engine_task.cancel()
    _engine_task = None
    _engine_stop = asyncio.Event()
    _last_seen_occupancy_id = -1
