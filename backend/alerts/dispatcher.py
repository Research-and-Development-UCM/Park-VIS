"""Alert dispatcher — delivers ``alert_events`` via the registered channel.

Phase B scope:
  - Webhook delivery only (email + mqtt come in phase E).
  - One-shot delivery on ``status='fired'``; no retries with backoff
    yet (that lands in phase F).
  - Manual retry endpoint resets ``status='fired'`` so the dispatcher
    picks it up again.  No max-attempts check in phase B; phase F
    adds the cap.

The dispatcher runs as an asyncio task started by
``backend.alerts.engine.start_background``.  It polls for
``status='fired'`` rows every ``POLL_INTERVAL_SECONDS`` (default 2s)
and processes them sequentially.  Phase F can introduce concurrency
and backoff; phase B keeps it simple so the first end-to-end slice
is easy to reason about.
"""

import asyncio
import datetime
import json
from typing import Optional

from sqlalchemy.orm import Session

from .. import database, models
from .channels import email as email_channel
from .channels import mqtt as mqtt_channel
from .channels import webhook as webhook_channel
from ..logging_config import vulture_logger as logger


POLL_INTERVAL_SECONDS = 2.0
CHANNEL_DISPATCH = {
    "webhook": webhook_channel.deliver,
    "email": email_channel.deliver,
    "mqtt": mqtt_channel.deliver,
}

# Exponential backoff schedule.  After attempts=N fails, the next
# attempt is scheduled at now + BACKOFF[N-1] seconds, provided N
# < MAX_ATTEMPTS.  The 4th failure (attempts=4) is terminal: the
# event stays in 'failed' state until a manual retry.  ``BACKOFF``
# is sized to ``MAX_ATTEMPTS - 1`` because no backoff is needed for
# the last attempt.
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (60, 300, 1800)  # 1m, 5m, 30m after attempts 1, 2, 3


_dispatcher_task: Optional[asyncio.Task] = None
_dispatcher_stop = asyncio.Event()


async def _deliver_one(db: Session, event: models.AlertEvent) -> None:
    """Attempt delivery of a single event.  Updates event status in place.

    This is an async function because the channel handlers are async
    (httpx webhook, aiosmtplib via asyncio.to_thread, aiomqtt via
    asyncio.to_thread).  We ``await`` the handler directly — calling
    ``loop.run_until_complete`` on the active event loop would raise
    ``RuntimeError: this event loop is already running`` (the bug the
    old sync version had).

    The DB operations in this function are sync (SQLAlchemy).  They
    briefly block the event loop but are fast in normal operation
    (in-process SQLite, single-row updates).  If we ever move off
    SQLite, wrap them in ``asyncio.to_thread``.
    """
    rule = db.query(models.AlertRule).filter_by(id=event.rule_id).first()
    if not rule:
        event.status = "failed"
        event.last_error = f"rule {event.rule_id} not found"
        event.last_attempt_at = datetime.datetime.now(datetime.UTC)
        event.attempts = (event.attempts or 0) + 1
        db.commit()
        return

    if not rule.enabled:
        # Rule was disabled between fire and delivery; mark as failed
        # so it shows up in history but doesn't retry.  A future
        # enhancement: a separate status 'cancelled' for clarity.
        event.status = "failed"
        event.last_error = "rule disabled"
        event.last_attempt_at = datetime.datetime.now(datetime.UTC)
        event.attempts = (event.attempts or 0) + 1
        db.commit()
        return

    if not rule.channel_id:
        event.status = "failed"
        event.last_error = "rule has no channel"
        event.last_attempt_at = datetime.datetime.now(datetime.UTC)
        event.attempts = (event.attempts or 0) + 1
        db.commit()
        return

    channel = db.query(models.AlertChannel).filter_by(id=rule.channel_id).first()
    if not channel or not channel.enabled:
        event.status = "failed"
        event.last_error = "channel missing or disabled"
        event.last_attempt_at = datetime.datetime.now(datetime.UTC)
        event.attempts = (event.attempts or 0) + 1
        db.commit()
        return

    handler = CHANNEL_DISPATCH.get(channel.channel_type)
    if handler is None:
        event.status = "failed"
        event.last_error = f"unsupported channel_type: {channel.channel_type}"
        event.last_attempt_at = datetime.datetime.now(datetime.UTC)
        event.attempts = (event.attempts or 0) + 1
        db.commit()
        return

    # Build the envelope.  Pull the rule's last_fired_at so the
    # payload includes a "since last fire" hint (useful for receivers
    # that maintain their own state).
    try:
        config = json.loads(channel.config or "{}")
    except (TypeError, ValueError):
        config = {}
    try:
        inner_payload = json.loads(event.payload or "{}")
    except (TypeError, ValueError):
        inner_payload = {}

    envelope = {
        "event_id": event.id,
        "rule_id": rule.id,
        "rule_name": rule.name,
        "trigger_type": rule.trigger_type,
        "fired_at": event.fired_at.isoformat() if event.fired_at else None,
        "data": inner_payload,
    }

    now = datetime.datetime.now(datetime.UTC)
    event.attempts = (event.attempts or 0) + 1
    event.last_attempt_at = now

    try:
        # Channel handlers are async.  We ``await`` directly so we
        # run on the active event loop (the dispatcher loop is async).
        # Channel handlers wrap their own blocking I/O in
        # ``asyncio.to_thread`` internally, so this doesn't block
        # other tasks.
        result = await handler(config, envelope)
    except Exception as exc:  # noqa: BLE001 — channel errors must not crash the loop
        logger.error("Alert delivery raised: {err}", err=exc)
        event.status = "failed"
        event.last_error = f"dispatcher exception: {exc!s}"
        # Schedule the next retry on the same backoff schedule the
        # non-exception failure path uses.  Without this, an
        # exception (vs. a clean ``result.success=False``) would
        # leave ``next_retry_at`` as NULL, and the claim query's
        # ``next_retry_at <= now`` would always be False in SQLite
        # (NULL comparisons), stranding the event in failed state.
        if event.attempts < MAX_ATTEMPTS:
            delay = BACKOFF_SECONDS[event.attempts - 1]
            event.next_retry_at = (
                datetime.datetime.now(datetime.UTC)
                + datetime.timedelta(seconds=delay)
            )
        else:
            event.next_retry_at = None  # terminal; no more retries
        db.commit()
        return

    if result.success:
        event.status = "sent"
        event.delivered_at = datetime.datetime.now(datetime.UTC)
        event.last_error = None
        event.next_retry_at = None
        logger.info(
            "Alert {eid} delivered via {chtype} (rule={rule_id})",
            eid=event.id, chtype=channel.channel_type, rule_id=rule.id,
        )
    else:
        event.status = "failed"
        event.last_error = result.error or "unknown error"
        # Schedule the next retry if we haven't hit the cap.
        if event.attempts < MAX_ATTEMPTS:
            delay = BACKOFF_SECONDS[event.attempts - 1]  # attempts is now 1..MAX
            event.next_retry_at = (
                datetime.datetime.now(datetime.UTC)
                + datetime.timedelta(seconds=delay)
            )
        else:
            event.next_retry_at = None  # terminal; no more retries
        logger.warning(
            "Alert {eid} delivery failed (attempt {n}/{max}, retry at {t}): {err}",
            eid=event.id, n=event.attempts, max=MAX_ATTEMPTS,
            t=event.next_retry_at, err=event.last_error,
        )
    db.commit()


def _claim_one_event(db: Session) -> Optional[models.AlertEvent]:
    """Find one event that's eligible for delivery now.

    An event is eligible when:
      - status == 'fired' (initial fire, never tried), OR
      - status == 'failed' AND next_retry_at <= now (retry time arrived)
      - AND it has attempts left (the retry budget isn't exhausted)

    ``sent`` events are not eligible.  ``failed`` events with a future
    ``next_retry_at`` are skipped (they'll be picked up later).

    A single dispatcher worker is fine with this non-atomic query.
    For multi-worker concurrency, wrap in a SELECT ... FOR UPDATE
    SKIP LOCKED or an atomic UPDATE ... WHERE status='fired'
    RETURNING — the rest of the dispatcher code is already
    structured for that swap.
    """
    from sqlalchemy import or_, and_
    now = datetime.datetime.now(datetime.UTC)
    return (
        db.query(models.AlertEvent)
        .filter(
            or_(
                models.AlertEvent.status == "fired",
                and_(
                    models.AlertEvent.status == "failed",
                    models.AlertEvent.next_retry_at <= now,
                ),
            ),
            # Skip events that have exhausted the retry budget.  The
            # retry endpoint enforces this too; filtering here avoids
            # a wasted dispatch attempt.
            models.AlertEvent.attempts < MAX_ATTEMPTS,
        )
        .order_by(models.AlertEvent.id.asc())
        .first()
    )


async def _dispatcher_loop() -> None:
    logger.info("Alert dispatcher started")
    while not _dispatcher_stop.is_set():
        try:
            db = database.SessionLocal()
            try:
                event = _claim_one_event(db)
                if event is not None:
                    # ``_deliver_one`` is async; awaiting here lets
                    # the channel handler run on this event loop
                    # without the ``run_until_complete`` deadlock
                    # the old sync version hit in production.
                    await _deliver_one(db, event)
                else:
                    # No work — sleep until the next poll.
                    pass
            finally:
                db.close()
        except asyncio.CancelledError:
            logger.info("Alert dispatcher cancelled")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("Alert dispatcher loop error: {err}", err=exc)

        try:
            await asyncio.wait_for(
                _dispatcher_stop.wait(), timeout=POLL_INTERVAL_SECONDS
            )
        except asyncio.TimeoutError:
            pass  # normal tick

    logger.info("Alert dispatcher stopped")


def start_background() -> None:
    """Start the dispatcher task.  Idempotent."""
    global _dispatcher_task, _dispatcher_stop
    if _dispatcher_task is not None and not _dispatcher_task.done():
        return
    _dispatcher_stop = asyncio.Event()
    _dispatcher_task = asyncio.create_task(_dispatcher_loop())



def stop_background() -> None:
    """Signal the dispatcher task to exit and wait briefly."""
    global _dispatcher_task
    _dispatcher_stop.set()
    # Don't await; lifespan shutdown is best-effort.
    _dispatcher_task = None


def reset_events() -> None:
    """Reset module-level singletons.  Used by tests."""
    global _dispatcher_task, _dispatcher_stop
    if _dispatcher_task is not None and not _dispatcher_task.done():
        _dispatcher_task.cancel()
    _dispatcher_task = None
    _dispatcher_stop = asyncio.Event()
