from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float
from sqlalchemy.orm import relationship, DeclarativeBase
import datetime

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True)
    # bcrypt hash is the only password storage. Legacy plaintext
    # support was removed (the app was never deployed with it).
    password_hash = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False) # Full access override
    permissions = Column(Text, default='[]') # JSON list of permission strings

class Camera(Base):
    __tablename__ = "cameras"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    snapshot_url = Column(String, nullable=True)
    source_type = Column(String, default="snapshot") # 'snapshot', 'rtsp', 'youtube', 'test', 'video'
    stream_url = Column(String, nullable=True) # RTSP link, YouTube URL, or Local File Path
    stream_user = Column(String, nullable=True)
    stream_password = Column(String, nullable=True)
    stream_resolution = Column(String, default="1080") # '720', '1080', '480', etc.
    stream_fps = Column(Integer, default=5) # Default to 5 FPS to save CPU
    youtube_mode = Column(String, default="stream") # 'stream' or 'thumbnail'
    is_test = Column(Boolean, default=False)
    local_path = Column(String, nullable=True)
    is_enabled = Column(Boolean, default=True)

    # Standardized relationship
    spaces = relationship("Space", back_populates="camera", cascade="all, delete-orphan")

class Space(Base):
    __tablename__ = "spaces"
    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), index=True)
    name = Column(String)
    points = Column(Text)   # JSON string of [x1, y1, x2, y2, …]
    
    # Standardized back-link
    camera = relationship("Camera", back_populates="spaces")

class CameraScan(Base):
    __tablename__ = "camera_scans"
    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), index=True)
    timestamp = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC), index=True)
    # image_blob removed - now in LMDB
    has_image = Column(Boolean, default=True, index=True)
    inference_speed = Column(Float, nullable=True) # in seconds
    camera = relationship("Camera")

class Occupancy(Base):
    __tablename__ = "occupancies"
    id = Column(Integer, primary_key=True)
    space_id = Column(Integer, ForeignKey("spaces.id"), index=True)
    scan_id = Column(Integer, ForeignKey("camera_scans.id"), index=True)
    occupied = Column(Boolean)
    space = relationship("Space")
    scan = relationship("CameraScan")

class OccupancyHourly(Base):
    __tablename__ = "occupancy_hourly"
    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), index=True)
    timestamp = Column(DateTime, index=True) # Start of the hour
    avg_occupied = Column(Integer) # Average number of occupied spots
    total_spaces = Column(Integer)
    avg_inference_speed = Column(Float, nullable=True) # Average speed for that hour
    camera = relationship("Camera")

class OccupancyEvent(Base):
    __tablename__ = "occupancy_events"
    id = Column(Integer, primary_key=True)
    space_id = Column(Integer, ForeignKey("spaces.id"), index=True)
    timestamp = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC), index=True)
    event_type = Column(String) # 'occupied' or 'vacated'
    # crop_blob removed - now in LMDB
    has_crop = Column(Boolean, default=True, index=True)
    confidence = Column(Float, nullable=True) # Model probability (0-1)
    space = relationship("Space")

class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(String)

class APIKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True)
    # The previous schema had a separate ``user_id`` for "the user
    # the key acts on behalf of", but the only writer (crud.create_api_key)
    # always sets it to the creator, and no query ever reads it. ``creator_id``
    # is the only ownership column used in the codebase.
    creator_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    key_prefix = Column(String) # First few chars for identification
    hashed_key = Column(String, unique=True, index=True)
    name = Column(String) # Friendly name like "My Laptop"
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))
    last_used_at = Column(DateTime, nullable=True)
    creator = relationship("User", foreign_keys=[creator_id])


# --- Alerting subsystem -------------------------------------------------------
# System-wide (no user_id scoping on the rows themselves; ownership is recorded
# via created_by_*/last_edited_by_* columns on each row so audit views can show
# who last touched the config even after a user is deleted).
#
# Lifecycle (3 states): fired -> sent | failed.  No acknowledgment workflow.
# Hysteresis + consecutive_count buffer prevent "lot full" spam.

class AlertChannel(Base):
    """Channel registry.  Rules pick from these by FK.

    Channel config holds the destination (URL, broker, email recipients).
    SMTP credentials live in the system-wide ``Setting`` table; the
    email channel only stores ``to`` addresses.
    """
    __tablename__ = "alert_channels"
    id = Column(Integer, primary_key=True)
    name = Column(String, index=True)
    channel_type = Column(String, index=True)  # 'webhook' | 'email' | 'mqtt'
    config = Column(Text, default='{}')        # JSON; shape depends on channel_type
    enabled = Column(Boolean, default=True)

    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))
    last_edited_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_username = Column(String, nullable=True)  # snapshot for audit
    last_edited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_edited_by_username = Column(String, nullable=True)


class CameraGroup(Base):
    """User-defined named set of cameras.  Threshold rules scope to a group."""
    __tablename__ = "camera_groups"
    id = Column(Integer, primary_key=True)
    name = Column(String, index=True)

    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))
    last_edited_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_username = Column(String, nullable=True)
    last_edited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_edited_by_username = Column(String, nullable=True)


class CameraGroupMembership(Base):
    __tablename__ = "camera_group_memberships"
    camera_group_id = Column(Integer, ForeignKey("camera_groups.id", ondelete="CASCADE"), primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True)


class AlertRule(Base):
    """A single rule.  System-wide; ownership tracked via audit columns.

    ``condition`` is a JSON blob whose shape depends on ``trigger_type``
    (see ``hysteresis.py`` for the canonical evaluators).  ``consecutive_count``
    is the per-space buffer; default 1 means "fire on first observation".
    ``cooldown_seconds`` is the orthogonal cool-off period (default 180s).
    """
    __tablename__ = "alert_rules"
    id = Column(Integer, primary_key=True)
    name = Column(String, index=True)
    trigger_type = Column(String, index=True)  # see plan: 6 trigger types
    condition = Column(Text, default='{}')     # JSON
    channel_id = Column(Integer, ForeignKey("alert_channels.id"), index=True, nullable=True)
    enabled = Column(Boolean, default=True)
    cooldown_seconds = Column(Integer, default=180)
    consecutive_count = Column(Integer, default=1)
    time_window = Column(Text, nullable=True)  # JSON: {days, start_hour, end_hour, tz} or null

    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))
    last_edited_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_username = Column(String, nullable=True)
    last_edited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_edited_by_username = Column(String, nullable=True)

    channel = relationship("AlertChannel")


class AlertEvent(Base):
    """Audit trail.  3-state lifecycle: fired -> sent | failed.  No ack."""
    __tablename__ = "alert_events"
    id = Column(Integer, primary_key=True)
    rule_id = Column(Integer, ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True)
    fired_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC), index=True)
    status = Column(String, default='fired', index=True)  # 'fired' | 'sent' | 'failed'
    payload = Column(Text, default='{}')                  # JSON snapshot of the firing event
    attempts = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    # When set on a 'failed' event, the dispatcher skips this row
    # until ``next_retry_at`` is in the past.  Phase F backoff.
    next_retry_at = Column(DateTime, nullable=True, index=True)
    rule = relationship("AlertRule")


class AlertState(Base):
    """Runtime per-rule state.  Persisted (survives restarts).

    One row per rule.  For per-space rules with multiple space_ids, per-space
    state lives in ``AlertStatePerSpace``; this row holds the rule-level
    aggregate (last_fired_at for cooldown, last_fired_event_id, etc.).
    """
    __tablename__ = "alert_state"
    rule_id = Column(Integer, ForeignKey("alert_rules.id", ondelete="CASCADE"), primary_key=True)
    state = Column(String, default='armed')  # 'armed' | 'buffering' | 'firing'
    consecutive_count_current = Column(Integer, default=0)
    last_observed_value = Column(Text, nullable=True)  # e.g. '0.92' for threshold, or '2026-01-01T00:00:00' for camera_offline last_seen_at
    last_fired_at = Column(DateTime, nullable=True)
    last_fired_event_id = Column(Integer, ForeignKey("alert_events.id"), nullable=True)


class AlertStatePerSpace(Base):
    """Per-(rule, space) runtime state for per-space edge rules only.

    Empty for threshold / camera_offline rules.  Composite PK.
    """
    __tablename__ = "alert_state_per_space"
    rule_id = Column(Integer, ForeignKey("alert_rules.id", ondelete="CASCADE"), primary_key=True)
    space_id = Column(Integer, ForeignKey("spaces.id", ondelete="CASCADE"), primary_key=True)
    state = Column(String, default='armed')  # 'armed' | 'buffering' | 'firing'
    consecutive_count_current = Column(Integer, default=0)
    last_observed_at = Column(DateTime, nullable=True)
    last_observed_occupied = Column(Boolean, nullable=True)
    firing_target = Column(Boolean, nullable=True)  # dynamic for space_edge


class AlertStatePerCamera(Base):
    """Per-(rule, camera) runtime state for camera-offline rules.

    Tracks which cameras are currently flagged as offline for this
    rule.  When a camera comes back online (new CameraScan row), the
    row is removed and the rule re-arms.
    """
    __tablename__ = "alert_state_per_camera"
    rule_id = Column(Integer, ForeignKey("alert_rules.id", ondelete="CASCADE"), primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True)
    offline_since = Column(DateTime, nullable=True)  # when the camera went offline

