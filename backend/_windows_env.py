"""Windows environment setup for bundled GStreamer runtime.

This module must be imported before any GStreamer or PyGObject imports.
It prepends the bundled GStreamer MinGW binaries to ``PATH``,
``GI_TYPELIB_PATH``, and ``GST_PLUGIN_PATH`` so that ``gi.repository``
can find the typelibs and DLLs shipped alongside the application.
"""

import os
import sys
import importlib.abc
import importlib.util

__all__ = ["setup_windows_env", "enforce_single_instance"]

GST_SUBDIR = "gstreamer"


def setup_windows_env() -> None:
    """Configure environment variables for a bundled GStreamer runtime.

    The expected layout relative to the executable (or ``_MEIPASS`` for
    PyInstaller builds) is::

        gstreamer/
            bin/                    # *.dll
            lib/
                gstreamer-1.0/     # plugin .dlls
                girepository-1.0/  # *.typelib files
    """
    if sys.platform != "win32":
        return

    base = _get_base_dir()

    gst_bin = os.path.join(base, GST_SUBDIR, "bin")
    gst_typelib = os.path.join(base, GST_SUBDIR, "lib", "girepository-1.0")
    gst_plugins = os.path.join(base, GST_SUBDIR, "lib", "gstreamer-1.0")

    _prepend_path("PATH", gst_bin)
    _prepend_path("GI_TYPELIB_PATH", gst_typelib)

    # Only load plugins from our bundled directory — prevent GStreamer from
    # scanning system paths (which could include incompatible plugins that
    # corrupt the GLib type system on headless Windows).
    os.environ["GST_PLUGIN_SYSTEM_PATH"] = ""
    os.environ["GST_PLUGIN_PATH"] = gst_plugins

    # Disable the GStreamer registry cache to avoid stale/corrupted entries
    # from previous runs with different plugin sets.  ``GST_REGISTRY_UPDATE=no``
    # prevents writing the cache after scanning — but GStreamer will still
    # READ a stale cache file from the default system location
    # (``%LOCALAPPDATA%\Microsoft\Windows\INetCache\gstreamer-1.0\``).  We
    # therefore also redirect ``GST_REGISTRY`` to a fresh file so the
    # scanner always sees the current plugin set.
    #
    # The registry FILE must be user-writable.  The install dir
    # (``C:\Program Files\ParkVIS\``) is NOT user-writable on a
    # non-admin install, and the ``commonappdata`` dir is
    # machine-wide shared state.  Use ``%LOCALAPPDATA%`` — it's
    # always per-user, always writable, and survives across reinstalls
    # (which is what we want — the cache is keyed to the plugin set,
    # which only changes on actual install/uninstall, not on
    # ``park-vis.exe`` invocations).
    #
    # ``GST_REGISTRY`` is a FILE path, not a directory.  GStreamer
    # uses ``g_unlink()`` (i.e. ``DeleteFileW``) on the existing
    # cache before writing the new one.  On Windows, ``DeleteFileW``
    # fails with ERROR_ACCESS_DENIED ("Permission denied") if the
    # path is a directory — even one the user owns — so we MUST
    # point at a file (we use ``registry.bin`` as a leaf name) and
    # ensure its parent directory exists and is user-writable.
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            registry_dir = os.path.join(
                local_app_data, "ParkVIS", "gstreamer-registry"
            )
            registry_path = os.path.join(registry_dir, "registry.bin")
        else:
            # LOCALAPPDATA is normally always set on Windows.  Fall
            # back to a per-user temp dir if it's somehow missing.
            registry_dir = os.path.join(
                os.path.expanduser("~"), ".park-vis", "gstreamer-registry"
            )
            registry_path = os.path.join(registry_dir, "registry.bin")
    else:
        # Linux/macOS: use XDG_RUNTIME_DIR or a per-user location.
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir:
            registry_dir = os.path.join(runtime_dir, "park-vis-gstreamer")
        else:
            registry_dir = os.path.join(
                os.path.expanduser("~"), ".cache", "park-vis-gstreamer"
            )
        registry_path = os.path.join(registry_dir, "registry.bin")
    os.makedirs(registry_dir, exist_ok=True)
    # Belt-and-braces: if a stale non-file (e.g. a directory or
    # zero-byte leftover) is at the cache path, remove it so
    # GStreamer can write a fresh one.  We catch the exception so
    # this never blocks startup.
    if os.path.exists(registry_path) and not os.path.isfile(registry_path):
        try:
            if os.path.isdir(registry_path):
                import shutil
                shutil.rmtree(registry_path)
            else:
                os.remove(registry_path)
        except OSError:
            pass
    os.environ["GST_REGISTRY"] = registry_path
    os.environ["GST_REGISTRY_UPDATE"] = "no"
    os.environ["GST_REGISTRY_FORK"] = "no"

    # On modern Windows (10+), SetDefaultDllDirectories locks DLL search
    # to known directories only — PATH is ignored.  Use AddDllDirectory
    # via os.add_dll_directory (Python 3.8+) so that the bundled DLLs
    # (libgomp-1.dll, etc.) are reachable when loading _vulturevision.pyd
    # or gi.repository modules.  ``LOAD_LIBRARY_SEARCH_DEFAULT_DIRS``
    # (``0x00001100``) enables the search to include: the exe's
    # directory, the system directories, AND any directories added
    # via AddDllDirectory.  Without this call, AddDllDirectory
    # registrations are silently ignored and the legacy search order
    # (which does NOT include AddDllDirectory dirs) is used instead.
    import ctypes
    ctypes.windll.kernel32.SetDefaultDllDirectories(0x00001100)
    for d in (gst_bin, gst_plugins, base):
        if os.path.isdir(d):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass  # already added or not supported

    # Meta-path hook: PyInstaller's frozen importer can't discover
    # _vulturevision.pyd (it failed to import during Wine-based analysis),
    # so we intercept the import and load it directly from the filesystem.
    _install_vulturevision_hook(base)


def _install_vulturevision_hook(base: str) -> None:
    """Install a meta_path finder that loads _vulturevision.pyd by path."""
    vv_pyd = os.path.join(base, "vulturevision", "_vulturevision.pyd")
    if not os.path.isfile(vv_pyd):
        return  # not bundled (dev mode on Linux); nothing to do

    class VultureVisionFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname not in ("_vulturevision", "vulturevision._vulturevision"):
                return None
            return importlib.util.spec_from_file_location(fullname, vv_pyd)

    # Insert early in meta_path so it runs before PyInstaller's FrozenImporter.
    sys.meta_path.insert(0, VultureVisionFinder())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_base_dir() -> str:
    """Return the application root directory.

    In a PyInstaller bundle this is ``sys._MEIPASS``; otherwise it is the
    directory containing the ``main.py`` entry point or the executable.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return str(sys._MEIPASS)
    return os.path.dirname(os.path.abspath(__file__))


def _prepend_path(env_var: str, new_path: str) -> None:
    """Prepend *new_path* to the environment variable *env_var*.

    The path is only added once and the separator is ``;`` on Windows.
    """
    if not os.path.isdir(new_path):
        return
    current = os.environ.get(env_var, "")
    parts = current.split(os.pathsep) if current else []
    # Avoid duplicates
    if new_path not in parts:
        parts.insert(0, new_path)
    os.environ[env_var] = os.pathsep.join(parts)


# ---------------------------------------------------------------------------
# Single-Instance Enforcement (Windows Named Mutex)
# ---------------------------------------------------------------------------

def enforce_single_instance(interactive: bool = True) -> object:
    """Enforce that only one instance of Park VIS runs on this machine.

    Uses a Windows Named Mutex (Global\\ParkVIS_SingleInstance_Mutex).
    The 'Global\\' namespace spans across Windows Service sessions (Session 0)
    and interactive desktop user sessions (Session 1+).

    If another instance is already running:
      - If running interactively, pops up a native Windows MessageBox informing
        the user and automatically opens their default browser to the dashboard.
      - Exits immediately with sys.exit(0).

    Returns:
      The mutex handle (must be kept referenced for process lifetime).
    """
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    ERROR_ALREADY_EXISTS = 183
    ERROR_ACCESS_DENIED = 5
    MUTEX_GLOBAL = "Global\\ParkVIS_SingleInstance_Mutex"
    MUTEX_LOCAL = "Local\\ParkVIS_SingleInstance_Mutex"

    kernel32 = ctypes.windll.kernel32

    # 1. Check if the Global mutex already exists
    h_existing = kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_GLOBAL)
    if h_existing:
        kernel32.CloseHandle(h_existing)
        _notify_already_running(interactive)
        sys.exit(0)

    # 2. Try to create the Global mutex
    h_mutex = kernel32.CreateMutexW(None, False, MUTEX_GLOBAL)
    last_err = kernel32.GetLastError()

    if last_err in (ERROR_ALREADY_EXISTS, ERROR_ACCESS_DENIED) and not h_mutex:
        _notify_already_running(interactive)
        sys.exit(0)
    elif last_err == ERROR_ALREADY_EXISTS:
        if h_mutex:
            kernel32.CloseHandle(h_mutex)
        _notify_already_running(interactive)
        sys.exit(0)

    if not h_mutex:
        # Fall back to Local mutex if Global creation failed (e.g. standard non-admin account)
        h_mutex = kernel32.CreateMutexW(None, False, MUTEX_LOCAL)
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            if h_mutex:
                kernel32.CloseHandle(h_mutex)
            _notify_already_running(interactive)
            sys.exit(0)

    return h_mutex


def _notify_already_running(interactive: bool = True) -> None:
    """Notify the user that Park VIS is already running and open the browser."""
    if not interactive:
        return
    try:
        import ctypes
        import webbrowser

        MB_OK = 0x00000000
        MB_ICONINFORMATION = 0x00000040
        MB_TOPMOST = 0x00040000
        MB_SETFOREGROUND = 0x00010000
        flags = MB_OK | MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND

        title = "Park VIS"
        message = (
            "Park VIS is already running on this machine (as a Windows Service or background process).\n\n"
            "Opening the web dashboard in your browser at:\n"
            "http://localhost:8000"
        )
        # Launch browser to the live dashboard
        webbrowser.open("http://localhost:8000")
        # Display native Windows modal message box
        ctypes.windll.user32.MessageBoxW(0, message, title, flags)
    except Exception:
        pass
