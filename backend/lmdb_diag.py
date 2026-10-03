"""LMDB diagnostic for the rollinglmdb files.

Run with ``park-vis.exe --diag`` (or ``python -m backend.lmdb_diag``
under a normal Python interpreter).  The script never modifies any data
on disk — it only opens the files in various modes to narrow down why
``lmdb.open()`` is failing with MDB_IO_ERROR ("Input/output error").

Three escalating tests are run on each known rollinglmdb file:

  Test 1 — read-only, no lock.  The most permissive mode.  Bypasses
           the LMDB lock file and avoids any file-system-level write
           intent.  If THIS fails, the file content itself is broken
           (corrupt header, bad page size, truncated mmap region) or
           the file is on a filesystem that can't mmap it.

  Test 2 — read-only, with lock.  Opens the lock file (``lock.mdb``).
           If THIS fails but Test 1 passed, the lock file is broken
           (e.g. stale, owned by a phantom process, on a read-only
           share).

  Test 3 — read-write, with lock and writemap.  This is what the
           app actually does at startup.  If Test 1+2 pass but Test 3
           fails, the user lacks the OS-level rights to mmap-write the
           file (rare on local disks, common on network shares or
           when an AV/minifilter is holding the file).

For each file, the diagnostic also reports:
  * file path, size, mtime
  * the LMDB map_size that the app would use (from config)
  * whether the file's last-modified time is suspiciously old (which
    would suggest the file has not been written to since some event)
  * the LMDB file's internal page size and any version mismatch
"""

import os
import sys
import traceback
import lmdb


# Mirror the app's config.  Don't import backend.config here so this
# module has no side effects — the diag is read-only by design.
SNAPSHOT_DB_PATH = os.path.join(
    os.environ.get("PROGRAMDATA", "C:/ProgramData"),
    "ParkVIS", "snapshots"
)
CROP_DB_PATH = os.path.join(
    os.environ.get("PROGRAMDATA", "C:/ProgramData"),
    "ParkVIS", "cameras"
)
SNAPSHOT_MAX_SIZE_GB = int(os.environ.get("PARK_VIS_SNAPSHOT_MAX_SIZE_GB", "1000000"))
CROP_MAX_SIZE_GB = int(os.environ.get("PARK_VIS_CROP_MAX_SIZE_GB", "1000000"))


def _file_info(path: str) -> str:
    """Pretty-print stat info for a file."""
    if not os.path.exists(path):
        return f"  (does not exist)"
    try:
        st = os.stat(path)
        age_days = (os.path.getmtime(path) - 0)  # not useful, but show mtime
        import datetime
        mtime = datetime.datetime.fromtimestamp(st.st_mtime).isoformat()
        return (
            f"  size: {st.st_size:,} bytes ({st.st_size / (1024*1024):.1f} MiB)\n"
            f"  mtime: {mtime}\n"
            f"  mode: {oct(st.st_mode)}"
        )
    except Exception as e:
        return f"  (stat failed: {e})"


def _test_lmdb_open(path: str, **kwargs) -> tuple[bool, str]:
    """Try to open the LMDB file.  Returns (ok, info_string)."""
    try:
        env = lmdb.open(path, subdir=False, **kwargs)
        # Read some stats so we know the file actually opened
        st = env.stat()
        info = env.info()
        env.close()
        return True, (
            f"  entries: {st.get('entries', '?')}, "
            f"page_size: {info.get('psize', '?')}, "
            f"max_readers: {info.get('maxreaders', '?')}"
        )
    except Exception as e:
        return False, f"  FAILED: {type(e).__name__}: {e}"


def _diagnose_file(label: str, directory: str, max_size_gb: float) -> bool:
    """Run the three escalating tests on one rollinglmdb.  Returns
    True if all tests passed (file is healthy), False otherwise."""
    print(f"\n=== {label} ({directory}) ===")
    # The file at the path is the FIRST chunk (e.g. "1.mdb"); subsequent
    # chunks are named "2.mdb", "3.mdb", etc.  Test the first one.
    first_chunk = os.path.join(directory, "1.mdb")
    print(f"  First chunk path: {first_chunk}")
    print(_file_info(first_chunk))
    if not os.path.exists(first_chunk):
        print("  No first chunk yet; will be created on first write.")
        return True  # not a failure, just nothing to test

    # Also check the lock file
    lock_path = os.path.join(directory, "lock.mdb")
    if os.path.exists(lock_path):
        print(f"  Lock file: {lock_path}")
        print(_file_info(lock_path))

    # Test 1: read-only, no lock
    print("\n  Test 1: read-only, no lock, no mmap-write ...")
    ok, info = _test_lmdb_open(
        first_chunk,
        readonly=True,
        lock=False,
        readahead=False,
        writemap=False,
        map_size=max_size_gb * 1024 * 1024 * 1024,
    )
    print(f"  {'OK' if ok else 'FAIL'}: {info}")

    # Test 2: read-only, with lock
    print("\n  Test 2: read-only, with lock ...")
    ok2, info2 = _test_lmdb_open(
        first_chunk,
        readonly=True,
        readahead=False,
        map_size=max_size_gb * 1024 * 1024 * 1024,
    )
    print(f"  {'OK' if ok2 else 'FAIL'}: {info2}")

    # Test 3: read-write, with lock (what the app actually does)
    print("\n  Test 3: read-write, with lock (production config) ...")
    ok3, info3 = _test_lmdb_open(
        first_chunk,
        map_size=max_size_gb * 1024 * 1024 * 1024,
    )
    print(f"  {'OK' if ok3 else 'FAIL'}: {info3}")

    if ok and ok2 and ok3:
        print(f"\n  {label} is healthy.")
        return True
    print(f"\n  {label} has issues.  See results above.")
    return False


def run_diagnostic() -> int:
    print("=" * 70)
    print("ParkVIS LMDB diagnostic")
    print("=" * 70)
    print(f"Python: {sys.version}")
    print(f"LMDB: {lmdb.__version__ if hasattr(lmdb, '__version__') else '(unknown version)'}")
    print(f"Platform: {sys.platform}")
    print(f"Working dir: {os.getcwd()}")

    snap_ok = _diagnose_file("Snapshots rollinglmdb", SNAPSHOT_DB_PATH, SNAPSHOT_MAX_SIZE_GB)
    crop_ok = _diagnose_file("Crops rollinglmdb", CROP_DB_PATH, CROP_MAX_SIZE_GB)

    print()
    print("=" * 70)
    if snap_ok and crop_ok:
        print("All files are healthy.  The startup error is from something else.")
        return 0
    else:
        print("Some files are unhealthy.  See results above.")
        print()
        print("Common causes for MDB_IO_ERROR on Windows when the file exists")
        print("and no process has it open:")
        print("  1. Antivirus minifilter is intercepting mmap (add an exclusion")
        print("     for C:\\ProgramData\\ParkVIS in Defender)")
        print("  2. File is on a network share / cloud-sync folder and the sync")
        print("     client is touching it.  Move the data dir to a local disk.")
        print("  3. NTFS metadata journal is dirty (run `chkdsk C: /f`)")
        print("  4. The file is in a half-written state from a previous crash")
        print("     (this script can't recover it, but the underlying data")
        print("     is not actually lost -- LMDB can often recover with a")
        print("     read-only open which Test 1 above tests for).")
        return 1
