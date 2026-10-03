r"""Resolve the on-prem storage root and ensure it exists.

On Linux: /var/lib/park-vis (fallback: ~/.park-vis)
On Windows: %PROGRAMDATA%\ParkVIS (fallback: ~/.park-vis)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_home_path() -> str:
    if sys.platform == "win32":
        program_data = os.environ.get("PROGRAMDATA", "C:\\ProgramData")
        return os.path.join(program_data, "ParkVIS")
    return "/var/lib/park-vis"


def lv_home() -> Path:
    """Return the resolved LV_HOME directory, creating it if needed."""
    raw = os.environ.get("PARK_VIS_HOME") or _default_home_path()
    candidate = Path(raw).expanduser()

    try:
        candidate.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(candidate, 0o700)
        except (OSError, NotImplementedError):
            pass
        return candidate
    except (PermissionError, OSError):
        # Fall back to user-home location when system path is unavailable.
        fallback = Path.home() / ".park-vis"
        if str(fallback) == str(candidate):
            return candidate
        fallback.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(fallback, 0o700)
        except (OSError, NotImplementedError):
            pass
        if not os.environ.get("PARK_VIS_HOME"):
            print(
                f"[billing] LV_HOME '{candidate}' not writable; using '{fallback}'",
                file=sys.stderr,
            )
        return fallback


def hwid_path() -> Path:
    return lv_home() / "hwid"


def session_path() -> Path:
    return lv_home() / "session.json"