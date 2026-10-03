import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import uvicorn
import argparse
import sys
import glob

# --- Windows Console / Foreground Mode ---
# When built with console=True (PyInstaller), a console window is allocated at
# startup.  If --foreground is NOT passed, hide it so the app behaves like a
# normal Windows GUI process.  Service mode (NSSM) doesn't attach a console,
# so this is a no-op there.
_foreground = False

if sys.platform == "win32":
    _parser = argparse.ArgumentParser(add_help=False)
    _parser.add_argument("-f", "--foreground", action="store_true")
    _foreground = _parser.parse_known_args()[0].foreground
    if not _foreground:
        import ctypes
        try:
            ctypes.windll.kernel32.FreeConsole()
        except Exception:
            pass

# --- Windows GStreamer Environment ---
# Must run before any backend imports touch gi.repository.
# The import is unconditional so PyInstaller's analysis (which runs
# on the Linux build host) still finds the module and includes its
# bytecode in the PYZ.  The setup itself is a no-op on non-Windows.
import importlib
import importlib.util
_instance_mutex = None

if sys.platform == "win32":
    from backend._windows_env import setup_windows_env, enforce_single_instance
    setup_windows_env()
    # Enforce single instance unless running diagnostic or help commands
    if not any(arg in sys.argv for arg in ("--diag", "-h", "--help")):
        _instance_mutex = enforce_single_instance(interactive=True)
else:
    # Import-only on non-Windows so PyInstaller's static analysis
    # picks the module up.  Calling setup_windows_env() would fail
    # on Linux (no ctypes.windll), so we just import and discard.
    from backend import _windows_env  # noqa: F401

# --- Linux Dynamic Linker Fix ---
# Only relevant on Linux where LD_LIBRARY_PATH isn't inherited across
# Python interpreter re-exec and GPU libraries live in a venv.
def ensure_gpu_libraries():
    if sys.platform == "win32":
        return
    # 1. Find our venv's site-packages
    try:
        site_packages = glob.glob(os.path.join(os.getcwd(), "venv/lib/python*/site-packages"))[0]
    except IndexError:
        return # Not in a standard venv setup, skip

    # 2. Collect all nvidia lib paths
    nvidia_lib_dirs = glob.glob(os.path.join(site_packages, "nvidia/*/lib"))
    if not nvidia_lib_dirs:
        return

    # 3. Check if these are already in LD_LIBRARY_PATH
    current_ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    missing_paths = [p for p in nvidia_lib_dirs if p not in current_ld_path]

    if missing_paths:
        # Update the environment
        new_ld_path = ":".join(nvidia_lib_dirs) + (":" + current_ld_path if current_ld_path else "")
        os.environ["LD_LIBRARY_PATH"] = new_ld_path
        
        # 4. RE-EXECUTE the process to force the dynamic linker to reload
        # This is the ONLY reliable way to change LD_LIBRARY_PATH on Linux once inside Python
        print(f"[INIT] Library path updated. Restarting process to apply GPU acceleration...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

# Run this before ANY other imports (like uvicorn or backend)
if not os.environ.get("PARK_VIS_REEXEC"):
    os.environ["PARK_VIS_REEXEC"] = "1"
    ensure_gpu_libraries()
else:
    os.environ.pop("PARK_VIS_REEXEC", None)

from backend.logging_config import setup_logging

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Park VIS Runner")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--profile-memory", action="store_true", help="Enable memory allocation tracing (High Overhead)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload")
    parser.add_argument(
        "--diag",
        action="store_true",
        help="Run LMDB diagnostic on the rollinglmdb files (snapshots/crops), print results, exit",
    )
    if sys.platform == "win32":
        parser.add_argument("-f", "--foreground", action="store_true", help="Run in foreground with visible console")
    args = parser.parse_args()

    if args.diag:
        from backend.lmdb_diag import run_diagnostic
        rc = run_diagnostic()
        sys.exit(rc)

    # 1. Initialize our logging BEFORE uvicorn starts
    from backend.config import config
    level = "DEBUG" if args.debug else config.LOG_LEVEL
    os.environ["PARK_VIS_LOG_LEVEL"] = level
    if args.profile_memory:
        os.environ["PARK_VIS_PROFILE_MEMORY"] = "true"

    setup_logging(level=level)
    
    # 2. Start uvicorn programmatically
    # Check if SSL is enabled in settings DB
    ssl_certfile = None
    ssl_keyfile = None
    try:
        from backend.database import SessionLocal
        from backend.models import Setting
        import sqlalchemy
        db = SessionLocal()
        try:
            enabled_setting = db.query(Setting).filter_by(key="ssl_enabled").first()
            if enabled_setting and enabled_setting.value.lower() == "true":
                cert_setting = db.query(Setting).filter_by(key="ssl_cert_path").first()
                key_setting = db.query(Setting).filter_by(key="ssl_key_path").first()
                if cert_setting and key_setting and os.path.exists(cert_setting.value) and os.path.exists(key_setting.value):
                    ssl_certfile = cert_setting.value
                    ssl_keyfile = key_setting.value
                    print(f"[SSL] SSL is enabled. Serving over HTTPS using cert: {ssl_certfile}")
        except sqlalchemy.exc.OperationalError:
            # Table might not exist yet if DB is uninitialized
            pass
        finally:
            db.close()
    except Exception as e:
        print(f"[SSL] Warning checking SSL configuration: {e}")

    # We set log_config=None to prevent uvicorn from overriding our setup
    uvicorn.run(
        "backend.app:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        log_config=None,
        reload_dirs=["backend"] if not args.no_reload else None,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile
    )
