import os
import sys
import secrets
import subprocess
from dotenv import load_dotenv


def _default_data_dir() -> str:
    """Platform-appropriate data directory for persistent files."""
    if sys.platform == "win32":
        program_data = os.environ.get("PROGRAMDATA", "C:\\ProgramData")
        return os.path.join(program_data, "ParkVIS")
    return "/var/lib/park-vis"


def _ensure_data_dir_writable_win32(data_dir: str) -> None:
    """Make sure ``data_dir`` is writable by the current user on Windows.

    The Inno Setup installer, when run elevated, creates
    ``C:\\ProgramData\\ParkVIS\\`` with an ACL that grants the
    ``Administrators`` group full control but only gives the
    regular user ``read & execute``.  That's enough for the desktop
    app to read the SQLite DB, but breaks the scheduler's
    ``INSERT INTO camera_scans`` with
    ``sqlite3.OperationalError: attempt to write a readonly
    database`` (which actually fires when SQLITE_OPEN_READONLY
    can't be cleared by the file ACL, not because the file itself
    is read-only — the file's ACL is what the user can write to
    is governed by).

    The fix: if the data dir exists but isn't writable by the
    current user, run ``icacls`` to grant the ``Users`` group
    modify access.  This is idempotent — running it on a
    already-correctly-permissioned dir is a no-op.

    ``icacls`` is built into Windows Vista+, so no extra
    dependency.  It may trigger a brief UAC prompt the FIRST
    time only (when the existing dir has restrictive ACLs and
    the user needs to elevate to change them).  Subsequent
    starts skip the ``icacls`` call.
    """
    if not os.path.isdir(data_dir):
        # Dir doesn't exist yet — let the Inno Setup / app
        # create-on-first-write path handle it.  The new
        # installer's [Dirs] section now includes
        # ``Permissions: users-modify`` so the new dir's ACLs
        # will be correct.
        return

    # Probe: can we create a file in the dir?
    probe = os.path.join(data_dir, ".lv-write-probe")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return  # dir is writable; nothing to do
    except OSError:
        pass  # dir is not writable; fall through to icacls

    # Try icacls.  We grant the BUILTIN\Users group modify
    # access recursively, then re-probe.
    #
    # ``(OI)(CI)M`` = Object Inherit + Container Inherit +
    # Modify — applies to this dir, all subdirs, and all files
    # in them.  ``/T`` makes the change recursive (redundant
    # with the (OI)(CI) flags, but harmless).
    popen_kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # CREATE_NO_WINDOW — don't pop a console window.
        popen_kwargs["creationflags"] = 0x08000000
    try:
        subprocess.run(
            [
                "icacls",
                data_dir,
                "/grant",
                "BUILTIN\\Users:(OI)(CI)M",
                "/T",
            ],
            check=True,
            **popen_kwargs,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        # Log but don't raise — the app's normal startup will
        # hit the same write error and log it more verbosely.
        # The most common cause is that the current process is
        # not elevated, so icacls silently fails.  Tell the
        # user how to fix it.
        sys.stderr.write(
            f"[config] WARNING: {data_dir!r} is not writable "
            f"by the current user.  This is the data dir for "
            f"ParkVIS and is needed for the scheduler, "
            f"LMDB, and the SQLite DB.  The dir was likely "
            f"created by an elevated installer with restrictive "
            f"ACLs.  To fix, run this in an elevated Command "
            f"Prompt ONCE:\n"
            f"  icacls \"{data_dir}\" /grant BUILTIN\\Users:(OI)(CI)M /T\n"
            f"Original error: {exc}\n"
        )
        return

    # Re-probe to confirm.
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        sys.stderr.write(
            f"[config] Widened ACLs on {data_dir!r} so the "
            f"current user can write to it (icacls).\n"
        )
    except OSError as exc:
        sys.stderr.write(
            f"[config] WARNING: {data_dir!r} is still not "
            f"writable after icacls: {exc}\n"
        )


def _resolve_data_dir() -> str:
    """The actual data directory in use, honoring ``PARK_VIS_HOME``.

    The two callers (``Config.LV_HOME`` and
    ``_load_or_create_secret_key``) must agree on the path so the
    secret key is persisted to the same directory the rest of the
    app reads from.
    """
    return os.getenv("PARK_VIS_HOME", _default_data_dir())


def _load_or_create_secret_key() -> tuple[str, str]:
    """Resolve the JWT signing key, with a persistent fallback.

    Resolution order (first wins):
      1. ``PARK_VIS_SECRET_KEY`` env var — explicit operator override
      2. ``$LV_HOME/secret_key`` file — generated on first start, 0600 perms
      3. Generate a new 48-byte URL-safe token, persist it to
         ``$LV_HOME/secret_key`` with 0600 perms, return it

    This guarantees we never fall back to a hardcoded default, which
    would let anyone with the source repo forge admin tokens. (See
    C1 in the security audit: ``backend/config.py:34`` previously
    defaulted to ``"change_me_later_for_production"``.)

    Returns ``(key, source)`` where ``source`` is one of
    ``"env"``, ``"file"``, ``"generated"`` — used by the startup
    log to warn operators when the env var isn't set.
    """
    env_val = os.getenv("PARK_VIS_SECRET_KEY")
    if env_val:
        return env_val, "env"

    data_dir = _resolve_data_dir()
    key_path = os.path.join(data_dir, "secret_key")

    try:
        os.makedirs(data_dir, exist_ok=True)
        if os.path.exists(key_path):
            with open(key_path, "r") as f:
                value = f.read().strip()
            if value:
                return value, "file"
    except OSError:
        # Read failure on a real install is bad, but we should not
        # silently fall through to a regenerated key (that would
        # invalidate every existing JWT). Bubble up so the operator
        # notices on startup.
        raise

    key = secrets.token_urlsafe(48)
    try:
        with open(key_path, "w") as f:
            f.write(key)
        os.chmod(key_path, 0o600)
    except OSError as exc:
        raise RuntimeError(
            f"Could not persist generated secret key to {key_path}: {exc}. "
            "Set PARK_VIS_SECRET_KEY explicitly or fix filesystem permissions."
        ) from exc
    return key, "generated"


def _resolve_app_version() -> str:
    """Resolve the application version.
    
    1. PARK_VIS_VERSION env var (injected by Docker or CI/CD build scripts)
    2. VERSION file in project root
    3. Git metadata (vYYYY.MM.DD-shortsha) if in a git repository
    4. Fallback to 'v-dev'
    """
    env_ver = os.getenv("PARK_VIS_VERSION")
    if env_ver:
        return env_ver
    
    version_file = os.path.join(os.path.dirname(__file__), "..", "VERSION")
    if os.path.exists(version_file):
        try:
            with open(version_file, "r") as f:
                v = f.read().strip()
                if v:
                    return v
        except Exception:
            pass

    try:
        from datetime import datetime, timezone
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(__file__)
        ).decode().strip()
        date_str = datetime.now(timezone.utc).strftime("%Y.%m.%d")
        return f"v{date_str}-{sha}"
    except Exception:
        pass

    return "v-dev"


# Load .env from DATA_DIR so deployed apps can persist overrides
load_dotenv(os.path.join(_resolve_data_dir(), ".env"))
# Also try CWD for dev convenience
load_dotenv()


class Config:
    APP_VERSION = _resolve_app_version()

    def __init__(self):
        self._snapshot_db_path = os.path.join(self.DATA_DIR, "snapshots")
        self._crop_db_path = os.path.join(self.DATA_DIR, "crops")

    # --- Logging & Instrumentation ---
    LOG_LEVEL = os.getenv("PARK_VIS_LOG_LEVEL", "DEBUG").upper()
    LOG_RETENTION_DAYS = int(os.getenv("PARK_VIS_LOG_RETENTION_DAYS", "5"))

    # OpenTelemetry / Jaeger
    OTEL_ENABLED = os.getenv("PARK_VIS_OTEL_ENABLED", "false").lower() == "true"
    OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    OTEL_SERVICE_NAME = os.getenv("PARK_VIS_SERVICE_NAME", "park-vis-backend")

    # Database Toggles
    SQL_ECHO = os.getenv("PARK_VIS_SQL_ECHO", "false").lower() == "true"
    SQL_WAL_MODE = os.getenv("PARK_VIS_SQL_WAL_MODE", "true").lower() == "true"

    # --- Security ---
    # PARK_VIS_SECRET_KEY env var wins; otherwise the key is loaded
    # from (or generated into) $LV_HOME/secret_key with 0600 perms.
    # Never defaults to a hardcoded constant — see C1 audit fix.
    _secret, _secret_source = _load_or_create_secret_key()
    SECRET_KEY = _secret
    SECRET_KEY_SOURCE = _secret_source

    # --- Inference ---
    PROFILE_MEMORY = os.getenv("PARK_VIS_PROFILE_MEMORY", "false").lower() == "true"

    # --- Data Directory ---
    LV_HOME = _resolve_data_dir()
    # On Windows, ensure the data dir is user-writable.  The
    # Inno Setup installer (run elevated) creates
    # ``C:\ProgramData\ParkVIS\`` with an ACL that lets the
    # regular user only read & execute — fine for the SQLite
    # SELECTs, but the scheduler's ``INSERT INTO camera_scans``
    # would fail with ``attempt to write a readonly database``
    # because the file's writable bit is set via the parent
    # directory's ACL, not the file's own.  This check is
    # idempotent and self-healing: it runs ``icacls`` only on
    # dirs that fail the write-probe.
    if sys.platform == "win32":
        _ensure_data_dir_writable_win32(LV_HOME)

    @property
    def DATA_DIR(self) -> str:
        return self.LV_HOME

    @DATA_DIR.setter
    def DATA_DIR(self, value: str) -> None:
        # Setters are mainly for tests that want to redirect the
        # data directory into a temp dir without restarting the
        # process.  Production code should set ``PARK_VIS_HOME``
        # before importing ``backend.config``.
        self.LV_HOME = value

    @property
    def LOG_DIR(self) -> str:
        return os.path.join(self.DATA_DIR, "logs")

    @property
    def SNAPSHOT_DB_PATH(self) -> str:
        return self._snapshot_db_path

    @SNAPSHOT_DB_PATH.setter
    def SNAPSHOT_DB_PATH(self, value: str) -> None:
        self._snapshot_db_path = value

    @property
    def CROP_DB_PATH(self) -> str:
        return self._crop_db_path

    @CROP_DB_PATH.setter
    def CROP_DB_PATH(self, value: str) -> None:
        self._crop_db_path = value

    @property
    def DB_PATH(self) -> str:
        return os.path.join(self.DATA_DIR, "parkinglot.db")

    SNAPSHOT_MAX_SIZE_GB = int(os.getenv("PARK_VIS_SNAPSHOT_MAX_SIZE_GB", "1000000"))
    CROP_MAX_SIZE_GB = int(os.getenv("PARK_VIS_CROP_MAX_SIZE_GB", "1000000"))

    # --- Billing Integration ---
    BILLING_PORTAL_URL = os.getenv("PARK_VIS_BILLING_PORTAL_URL", "https://license.lotvulture.com")
    HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("PARK_VIS_HEARTBEAT_INTERVAL_SECONDS", "600"))

config = Config()
