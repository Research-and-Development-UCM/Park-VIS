from pydantic import BaseModel, ConfigDict, field_validator, field_serializer
from typing import List, Optional
import datetime
import json


def _serialize_utc_datetime(dt: datetime.datetime) -> str:
    """Serialize a datetime as an ISO 8601 string with UTC timezone.

    SQLite strips timezone info on round-trip, so model
    attributes that were written as ``datetime.now(UTC)`` come
    back as naive datetimes.  Without a timezone marker on the
    wire, the frontend's ``new Date(iso)`` interprets naive
    ISO strings as local time, which makes the browser show
    the time wrong by the user's UTC offset.

    This serializer pins every datetime to UTC and emits the
    ``Z`` suffix so the frontend reliably interprets it as
    UTC and ``toLocaleString()`` converts to the user's local
    time correctly.

    Naive datetimes are assumed to be UTC (the backend always
    writes with ``datetime.now(UTC)`` or equivalent); if a
    timezone-aware datetime arrives in some other zone, we
    convert to UTC for a single canonical representation.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    else:
        dt = dt.astimezone(datetime.UTC)
    # ``isoformat()`` on a UTC datetime emits "+00:00"; the
    # frontend's Date parser accepts both, but ``Z`` is shorter
    # and the de-facto convention for UTC.
    return dt.isoformat().replace("+00:00", "Z")

class LoginForm(BaseModel):
    username: str
    password: str

class CameraCreate(BaseModel):
    name: str
    snapshot_url: Optional[str] = None
    source_type: Optional[str] = "snapshot"
    stream_url: Optional[str] = None
    stream_user: Optional[str] = None
    stream_password: Optional[str] = None
    stream_resolution: Optional[str] = "1080"
    stream_fps: Optional[int] = 5
    youtube_mode: Optional[str] = "stream"
    is_test: bool = False
    local_path: Optional[str] = None
    is_enabled: bool = True

class CameraOut(BaseModel):
    id: int
    name: str
    snapshot_url: Optional[str] = None
    source_type: str
    stream_url: Optional[str] = None
    stream_user: Optional[str] = None
    stream_password: Optional[str] = None
    stream_resolution: Optional[str] = "1080"
    stream_fps: Optional[int] = 5
    youtube_mode: Optional[str] = "stream"
    is_test: bool
    local_path: Optional[str] = None
    is_enabled: bool

    model_config = ConfigDict(from_attributes=True)


class CameraOutPublic(BaseModel):
    """Camera response shape safe to return to any authenticated user.

    Excludes ``stream_password`` (C4 audit fix) so RTSP credentials
    never leave the server for low-privilege "view only" accounts.
    Use ``CameraOut`` (the full schema) for admin-only routes that
    need to read or write the credential.
    """
    id: int
    name: str
    snapshot_url: Optional[str] = None
    source_type: str
    stream_url: Optional[str] = None
    stream_user: Optional[str] = None
    # stream_password intentionally omitted
    stream_resolution: Optional[str] = "1080"
    stream_fps: Optional[int] = 5
    youtube_mode: Optional[str] = "stream"
    is_test: bool
    local_path: Optional[str] = None
    is_enabled: bool

    model_config = ConfigDict(from_attributes=True)

class SpaceBase(BaseModel):
    camera_id: int
    name: str
    points: List[float]

class SpaceCreate(SpaceBase):
    pass

class SpaceUpdate(SpaceBase):
    id: Optional[int] = None

class Space(SpaceBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

class StatItem(BaseModel):
    space_id: int
    timestamp: datetime.datetime
    occupied: bool

    @field_serializer("timestamp")
    def _ser_timestamp(self, dt):
        return _serialize_utc_datetime(dt)

class Setting(BaseModel):
    key: str
    value: str
    model_config = ConfigDict(from_attributes=True)

class SettingUpdate(BaseModel):
    value: str

class PasswordChange(BaseModel):
    old_password: str
    new_password: str


class UserBase(BaseModel):
    username: str
    is_admin: bool = False
    permissions: List[str]
class UserCreate(UserBase):
    password: str


class FirstAdminCreate(BaseModel):
    """Body for ``POST /api/setup/admin`` — creates the very first
    administrator on a fresh install (C3 audit fix).

    No ``is_admin`` field: the endpoint always sets it to ``True``.
    No ``permissions`` field: the first admin always gets full access
    via the ``is_admin`` flag (which short-circuits the permission
    check in ``auth.check_permission_direct``).
    """
    username: str
    password: str
    accept_eula: bool


class SetupStatus(BaseModel):
    """Body for ``GET /api/setup/status`` — used by the frontend
    to decide whether to render the Login or the /setup page.
    """
    setup_required: bool
    has_any_user: bool
    eula_accepted: bool


class UserUpdate(BaseModel):
    password: Optional[str] = None
    is_admin: Optional[bool] = None
    permissions: Optional[List[str]] = None

class UserOut(UserBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

class UserCurrent(UserBase):
    id: int

class APIKeyBase(BaseModel):
    name: str

class APIKeyCreate(APIKeyBase):
    pass

class APIKeyOut(APIKeyBase):
    id: int
    key_prefix: str
    created_at: datetime.datetime
    last_used_at: Optional[datetime.datetime] = None
    creator_username: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "last_used_at")
    def _ser_datetimes(self, dt):
        return _serialize_utc_datetime(dt)

class APIKeyGenerated(APIKeyOut):
    full_key: str # Only returned once upon creation

class FeedbackROI(BaseModel):
    space_id: int
    points: List[float]
    occupied: bool
    # The AI's original prediction for this space — sent so the
    # backend can compute which spots the user actually corrected
    # (without re-querying the Occupancy table, which may have been
    # pruned by retention by the time feedback is submitted). The
    # ML pipeline uses this to compute false-positive / false-
    # negative rates per camera.
    original_occupied: Optional[bool] = None


class FeedbackSubmit(BaseModel):
    scan_id: int
    camera_id: int
    spaces: List[FeedbackROI]


# --- Alerting subsystem schemas ---------------------------------------------
# The condition JSON shape is enforced by validators in
# ``backend.alerts.routes`` (per trigger_type), not by Pydantic — keeps the
# rule form free to evolve without breaking existing rows.

class AlertChannelBase(BaseModel):
    name: str
    channel_type: str  # 'webhook' | 'email' | 'mqtt' (validated server-side)
    config: dict = {}
    enabled: bool = True


class AlertChannelCreate(AlertChannelBase):
    pass


class AlertChannelUpdate(BaseModel):
    name: Optional[str] = None
    channel_type: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None


class AlertChannelOut(AlertChannelBase):
    id: int
    created_at: datetime.datetime
    last_edited_at: Optional[datetime.datetime] = None
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    last_edited_by_user_id: Optional[int] = None
    last_edited_by_username: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "last_edited_at")
    def _ser_datetimes(self, dt):
        return _serialize_utc_datetime(dt)

    @field_validator("config", mode="before")
    @classmethod
    def _parse_config(cls, v):
        """The DB column is TEXT (JSON).  Coerce to dict for the API."""
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return {}
        return v


class AlertRuleBase(BaseModel):
    name: str
    trigger_type: str  # 'space_occupied' | 'space_vacated' | 'space_edge' | 'lot_full_above_pct' | 'lot_open_below_pct' | 'camera_offline'
    condition: dict = {}
    channel_id: Optional[int] = None
    enabled: bool = True
    cooldown_seconds: int = 180
    consecutive_count: int = 1
    time_window: Optional[dict] = None


class AlertRuleCreate(AlertRuleBase):
    pass


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    trigger_type: Optional[str] = None
    condition: Optional[dict] = None
    channel_id: Optional[int] = None
    enabled: Optional[bool] = None
    cooldown_seconds: Optional[int] = None
    consecutive_count: Optional[int] = None
    time_window: Optional[dict] = None


class AlertRuleOut(AlertRuleBase):
    id: int
    created_at: datetime.datetime
    last_edited_at: Optional[datetime.datetime] = None
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    last_edited_by_user_id: Optional[int] = None
    last_edited_by_username: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "last_edited_at")
    def _ser_datetimes(self, dt):
        return _serialize_utc_datetime(dt)

    @field_validator("condition", mode="before")
    @classmethod
    def _parse_condition(cls, v):
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return {}
        return v

    @field_validator("time_window", mode="before")
    @classmethod
    def _parse_time_window(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return None
        return v


class AlertEventOut(BaseModel):
    id: int
    rule_id: int
    fired_at: datetime.datetime
    status: str
    payload: dict
    attempts: int
    last_error: Optional[str] = None
    last_attempt_at: Optional[datetime.datetime] = None
    delivered_at: Optional[datetime.datetime] = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("fired_at", "last_attempt_at", "delivered_at")
    def _ser_datetimes(self, dt):
        return _serialize_utc_datetime(dt)

    @field_validator("payload", mode="before")
    @classmethod
    def _parse_payload(cls, v):
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return {}
        return v


class CameraGroupBase(BaseModel):
    name: str
    camera_ids: List[int] = []


class CameraGroupCreate(CameraGroupBase):
    pass


class CameraGroupUpdate(BaseModel):
    name: Optional[str] = None
    camera_ids: Optional[List[int]] = None


class CameraGroupOut(CameraGroupBase):
    id: int
    created_at: datetime.datetime
    last_edited_at: Optional[datetime.datetime] = None
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    last_edited_by_user_id: Optional[int] = None
    last_edited_by_username: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "last_edited_at")
    def _ser_datetimes(self, dt):
        return _serialize_utc_datetime(dt)

