"""Pure state-machine evaluators for alert rules.

These functions take a state-machine snapshot + a fresh observation,
and return the next snapshot + a ``fire`` flag.  No DB, no I/O, no
time side effects (callers pass the current ``now`` if needed).  That
makes them trivial to unit-test.

The state machine has three states:

- ``armed`` — waiting for the firing condition.
- ``buffering`` — firing condition is currently observed; counting up
  toward ``consecutive_count``.
- ``firing`` — alert has been sent; no re-fire while the firing
  condition persists.  Re-arm on transition to the opposite state.

For per-space edge rules, the buffer counts *consecutive observations
in the firing state*.  A state change to the opposite resets the
counter.  This is exactly the "N consecutive occupied" pattern.

For ``space_occupied`` and ``space_vacated`` the firing target is
fixed (always True or always False).  For ``space_edge`` the firing
target is dynamic — it tracks the most recent observed state, so the
buffer counts "consecutive stable observations after a change" and
re-arms on the next change.

For threshold rules, the buffer is not used; instead hysteresis uses
two boundaries (``fire_at_pct`` and ``resolve_at_pct``) so that small
oscillations around ``fire_at_pct`` do not re-fire.
"""

from dataclasses import dataclass
from typing import Optional


# --- Per-space edge rule state machine ---------------------------------------

# Fixed firing targets for the directional trigger types.  ``space_edge``
# has no fixed target; the state machine derives it from the most recent
# observation.
FIXED_FIRING_TARGET = {
    "space_occupied": True,
    "space_vacated": False,
}


@dataclass
class SpaceRuleState:
    """Per-(rule, space) runtime state for per-space edge rules.

    ``state`` is one of 'armed' | 'buffering' | 'firing'.
    ``consecutive_count_current`` resets to 0 on transition to the
    non-firing observation; increments each tick the firing observation
    holds.

    ``firing_target`` is the state we're currently counting as "in the
    firing direction".  Fixed for ``space_occupied`` / ``space_vacated``
    (always True / always False); dynamic for ``space_edge`` (the most
    recent observed state).
    """
    state: str = "armed"
    consecutive_count_current: int = 0
    last_observed_occupied: Optional[bool] = None
    firing_target: Optional[bool] = None


@dataclass
class SpaceRuleResult:
    """Return value from :func:`evaluate_space_rule`."""
    new_state: SpaceRuleState
    fire: bool
    observation_was_firing: bool


def evaluate_space_rule(
    state: SpaceRuleState,
    observed_occupied: bool,
    trigger_type: str,
    consecutive_count: int,
) -> SpaceRuleResult:
    """Advance the per-space state machine by one observation.

    ``consecutive_count`` is the rule's buffer; 1 means "fire on the
    first observation in the firing state", 3 means "fire on the third
    consecutive observation in the firing state".  The counter resets
    on transition to the non-firing observation.
    """
    if trigger_type not in FIXED_FIRING_TARGET and trigger_type != "space_edge":
        raise ValueError(f"Unsupported per-space trigger_type: {trigger_type!r}")

    # Determine the firing target for this observation.
    if trigger_type == "space_edge":
        # Dynamic: use the existing target if set, otherwise seed it
        # with the current observation (so the first observation is
        # always counted, never discarded as a "baseline").
        firing_target = (
            state.firing_target
            if state.firing_target is not None
            else observed_occupied
        )
    else:
        firing_target = FIXED_FIRING_TARGET[trigger_type]

    is_firing = (observed_occupied == firing_target)

    new = SpaceRuleState(
        state=state.state,
        consecutive_count_current=state.consecutive_count_current,
        last_observed_occupied=observed_occupied,
        firing_target=firing_target,
    )

    fire = False

    if state.state == "firing":
        # Alert already sent for this breach.  Stay firing while the
        # observation is still on the firing side; re-arm on the
        # opposite observation.
        if not is_firing:
            if trigger_type == "space_edge":
                # The state changed direction.  Fire for the NEW
                # edge (not silently re-arm) and set the state to
                # "firing" so the next same-direction observation
                # doesn't refire.  Without this, the user sees
                # ``occupied → occupied → occupied`` fires with a
                # silent vacant transition in between, because the
                # vacant observation just rearms and the next
                # occupied observation is the one that fires.
                new.firing_target = observed_occupied
                new.consecutive_count_current = 0
                new.state = "firing"
                fire = True
            else:
                # space_occupied/space_vacated: the firing target
                # is fixed, so a non-firing observation is just a
                # return to the baseline.  Re-arm silently and let
                # the next in-direction observation re-fire the
                # buffer.
                new.state = "armed"
                new.consecutive_count_current = 0
        # else: stay firing, no refire, no counter change
        return SpaceRuleResult(new_state=new, fire=fire, observation_was_firing=is_firing)

    # armed or buffering — observation drives the buffer
    if not is_firing:
        if trigger_type == "space_edge":
            # State changed direction mid-buffer.  Start a fresh
            # buffer in the new direction (count=1).  With N=1, fire
            # immediately; with N>1, keep counting.
            new.state = "buffering"
            new.consecutive_count_current = 1
            new.firing_target = observed_occupied
            if consecutive_count <= 1:
                new.state = "firing"
                fire = True
        else:
            # space_occupied/space_vacated: a non-firing observation
            # means the buffer is invalidated.  Back to armed.
            new.state = "armed"
            new.consecutive_count_current = 0
        return SpaceRuleResult(new_state=new, fire=fire, observation_was_firing=is_firing)

    # is_firing == True from here.
    if state.state == "armed":
        new.state = "buffering"
        new.consecutive_count_current = 1
    else:  # state.state == "buffering"
        new.consecutive_count_current = state.consecutive_count_current + 1

    if new.consecutive_count_current >= consecutive_count:
        new.state = "firing"
        fire = True
        # Reset the buffer for the next breach (will accumulate again
        # on re-arm).  Callers may also reset consecutive_count to 0
        # externally when they create the alert_event; resetting here
        # keeps the state machine self-consistent.

    return SpaceRuleResult(new_state=new, fire=fire, observation_was_firing=True)


# --- Threshold rule state machine --------------------------------------------

@dataclass
class ThresholdRuleState:
    """Per-rule runtime state for threshold rules (lot-full / lot-open).

    ``last_observed_value`` is the most recent occupancy % (0..100) as a
    string for storage portability.  ``state`` is 'armed' | 'firing'.
    """
    state: str = "armed"
    last_observed_value: Optional[float] = None
    last_fired_at: Optional[str] = None  # ISO timestamp, filled by caller


@dataclass
class ThresholdRuleResult:
    """Return value from :func:`evaluate_threshold_rule`."""
    new_state: ThresholdRuleState
    fire: bool
    in_firing_band: bool


def evaluate_threshold_rule(
    state: ThresholdRuleState,
    current_pct: float,
    trigger_type: str,
    fire_at_pct: float,
    resolve_at_pct: float,
) -> ThresholdRuleResult:
    """Advance the threshold-rule state machine by one observation.

    For ``lot_full_above_pct``: firing band is ``current_pct >= fire_at_pct``;
    resolved band is ``current_pct <= resolve_at_pct``.  The
    ``resolve_at_pct`` must be strictly less than ``fire_at_pct`` (or
    the rule is misconfigured and we'll never re-arm).

    For ``lot_open_below_pct``: firing band is ``current_pct <= fire_at_pct``;
    resolved band is ``current_pct >= resolve_at_pct``.  The
    ``resolve_at_pct`` must be strictly greater than ``fire_at_pct``.

    Values in the dead band (between the two thresholds) preserve the
    current state — this is what stops 89%→90%→89% oscillations from
    re-firing.
    """
    if trigger_type == "lot_full_above_pct":
        if resolve_at_pct >= fire_at_pct:
            raise ValueError(
                f"lot_full_above_pct: resolve_at_pct ({resolve_at_pct}) must be "
                f"strictly less than fire_at_pct ({fire_at_pct})"
            )
        in_firing = current_pct >= fire_at_pct
        in_resolved = current_pct <= resolve_at_pct
    elif trigger_type == "lot_open_below_pct":
        if resolve_at_pct <= fire_at_pct:
            raise ValueError(
                f"lot_open_below_pct: resolve_at_pct ({resolve_at_pct}) must be "
                f"strictly greater than fire_at_pct ({fire_at_pct})"
            )
        in_firing = current_pct <= fire_at_pct
        in_resolved = current_pct >= resolve_at_pct
    else:
        raise ValueError(f"Unsupported threshold trigger_type: {trigger_type!r}")

    new = ThresholdRuleState(
        state=state.state,
        last_observed_value=current_pct,
        last_fired_at=state.last_fired_at,
    )

    fire = False

    if state.state == "armed":
        if in_firing:
            new.state = "firing"
            fire = True
    else:  # state.state == "firing"
        if in_resolved:
            new.state = "armed"

    return ThresholdRuleResult(new_state=new, fire=fire, in_firing_band=in_firing)


# --- Cooldown gate -----------------------------------------------------------

def cooldown_elapsed(
    last_fired_at_iso: Optional[str],
    now_iso: str,
    cooldown_seconds: int,
) -> bool:
    """Return True if the cooldown period has elapsed since the last fire.

    ``last_fired_at_iso`` and ``now_iso`` are ISO 8601 strings.  If
    there is no prior fire, this always returns True.  Cooldown is a
    hard backstop *after* the state machine decides to fire; it does
    not change the state itself.

    SQLite (and some other backends) strip timezone info on read, so
    we defensively normalize both sides to offset-aware UTC before
    subtracting.
    """
    if not last_fired_at_iso:
        return True
    from datetime import datetime, timezone
    last = datetime.fromisoformat(last_fired_at_iso)
    now = datetime.fromisoformat(now_iso)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() >= cooldown_seconds


# --- Time window gate --------------------------------------------------------

def within_time_window(
    now_iso: str,
    time_window: Optional[dict],
) -> bool:
    """Return True if the observation falls inside the rule's time window.

    ``time_window`` is a dict shaped like::

        {
            "days": [0, 1, 2, 3, 4],   # 0=Monday ... 6=Sunday
            "start_hour": 8,            # inclusive, 0-23, in tz
            "end_hour": 18,             # exclusive, 0-24, in tz
            "tz": "America/Los_Angeles"
        }

    ``None`` means "always fire" (no window).
    """
    if not time_window:
        return True

    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.fromisoformat(now_iso)
    tz_name = time_window.get("tz") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    local = now.astimezone(tz)

    days = time_window.get("days")
    if days is not None and local.weekday() not in days:
        return False

    start_hour = time_window.get("start_hour", 0)
    end_hour = time_window.get("end_hour", 24)
    hour = local.hour
    # Support overnight windows: a night window (e.g. start=22, end=6)
    # used to never match because the condition ``start <= h < end``
    # is false whenever end < start. The wrap case is when
    # ``start > end`` (intentionally crossing midnight).
    if start_hour <= end_hour:
        if not (start_hour <= hour < end_hour):
            return False
    else:
        if not (hour >= start_hour or hour < end_hour):
            return False

    return True
