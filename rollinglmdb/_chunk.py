import os
import sys
import time
import lmdb
from typing import Iterator, Optional

META_KEY_LAST_EVENT_TIME = b"__meta:last_event_time__"

# Platform-specific LMDB defaults.
#
# Linux's mmap-for-write is reliable: a crash mid-write leaves the
# file consistent at the page granularity.  Windows mmap-for-write is
# NOT safe for the same guarantees.  When the system crashes, the
# process is killed, or the OS loses power, an LMDB file opened with
# ``writemap=True`` on Windows can end up with a torn write — the
# exact failure mode we hit on the user's install (the data file
# opens fine without the lock but fails with it; the lock file ends
# up corrupt).
#
# The safer Windows config uses ``writemap=False`` (writes go through
# the regular ``write()`` syscall, which is atomic at the page
# granularity on NTFS), synchronous mmap syncing, and fsync on
# every commit.  It's noticeably slower for heavy-write workloads
# but crash-safe.  Camera surveillance is read-mostly so the cost is
# negligible.
if sys.platform == "win32":
    DEFAULT_LMDB_KWARGS: dict = {
        "writemap": False,    # safe: write() per page, atomic on NTFS
        "map_async": False,   # sync the mmap synchronously
        "metasync": True,     # fsync metadata on commit
        "sync": True,         # fsync data on commit
        "readahead": False,
    }
else:
    DEFAULT_LMDB_KWARGS: dict = {
        "writemap": True,
        "map_async": True,
        "metasync": False,
        "sync": False,
        "readahead": False,
    }


class LmdbChunk:
    """A single LMDB database file.

    Each chunk is one ``.mdb`` file on disk (``subdir=False``).  The chunk
    holds an unnamed (single) LMDB database whose entries are keyed by
    opaque binary strings.
    """

    _FORBIDDEN_KWARGS = frozenset({"subdir", "map_size"})

    def __init__(self, path: str, epoch_start: int, map_size: int, **lmdb_kwargs):
        if not path.endswith(".mdb"):
            path = path + ".mdb"
        self.path = path
        self.epoch_start = epoch_start
        self._map_size = map_size
        merged = {**DEFAULT_LMDB_KWARGS}
        merged.update(lmdb_kwargs)
        # strip any kwargs that would conflict with the explicit params
        # passed to lmdb.open() below
        self._kwargs = {
            k: v for k, v in merged.items() if k not in self._FORBIDDEN_KWARGS
        }
        self._env: Optional[lmdb.Environment] = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        if self._env is not None:
            return
        self._env = lmdb.open(
            self.path,
            map_size=self._map_size,
            subdir=False,
            **self._kwargs,
        )

    def close(self) -> None:
        if self._env is not None:
            self._env.close()
            self._env = None

    @property
    def is_open(self) -> bool:
        return self._env is not None

    # ------------------------------------------------------------------
    # read / write
    # ------------------------------------------------------------------

    def put(self, key: str, value: bytes, timestamp: Optional[int] = None) -> None:
        if self._env is None:
            raise RuntimeError("chunk not open")
        with self._env.begin(write=True) as txn:
            txn.put(key.encode(), value)
            event_time = timestamp if timestamp is not None else int(time.time())
            txn.put(META_KEY_LAST_EVENT_TIME, str(event_time).encode())

    def get(self, key: str) -> Optional[bytes]:
        if self._env is None:
            raise RuntimeError("chunk not open")
        with self._env.begin() as txn:
            return txn.get(key.encode())

    def iterate(self) -> Iterator[tuple[str, bytes]]:
        if self._env is None:
            raise RuntimeError("chunk not open")
        with self._env.begin() as txn:
            with txn.cursor() as cursor:
                for k, v in cursor:
                    if not k.startswith(b"__meta:"):
                        yield (k.decode(), v)

    def iterkeys(self) -> Iterator[str]:
        """Yield keys only — much faster than :meth:`iterate` because
        values (which can be large image blobs) are never loaded."""
        if self._env is None:
            raise RuntimeError("chunk not open")
        with self._env.begin() as txn:
            with txn.cursor() as cursor:
                for k in cursor.iternext(keys=True, values=False):
                    if not k.startswith(b"__meta:"):
                        yield k.decode()

    def iterkeys_page(self, offset: int, limit: int) -> Iterator[tuple[str, int]]:
        """Yield ``(key, size_bytes)`` for a slice of entries, skipping
        *offset* items and yielding at most *limit* items.  A fast
        keys-only scan collects all keys first, then only the page's
        values are loaded individually — avoids loading every image
        blob from the entire chunk."""
        if self._env is None:
            raise RuntimeError("chunk not open")
        if limit <= 0:
            return
        with self._env.begin() as txn:
            # 1. Fast pass — collect keys without loading values.
            all_keys: list[bytes] = []
            with txn.cursor() as cursor:
                for k in cursor.iternext(keys=True, values=False):
                    if not k.startswith(b"__meta:"):
                        all_keys.append(k)
            # 2. Load values only for the requested page.
            page_keys = all_keys[offset:offset + limit]
            for k_bytes in page_keys:
                val = txn.get(k_bytes)
                if val is not None:
                    yield (k_bytes.decode(), len(val))

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------

    @property
    def last_event_time(self) -> int:
        """Timestamp of the newest entry written to this chunk, stored directly
        within the LMDB database file."""
        if self._env is not None:
            try:
                with self._env.begin() as txn:
                    val = txn.get(META_KEY_LAST_EVENT_TIME)
                    if val is not None:
                        return int(val.decode())
            except Exception:
                pass
        return self.epoch_start


    @property
    def num_entries(self) -> int:
        if self._env is None:
            return 0
        total = self._env.stat()["entries"]
        try:
            with self._env.begin() as txn:
                if txn.get(META_KEY_LAST_EVENT_TIME) is not None:
                    return max(0, total - 1)
        except Exception:
            pass
        return total

    @property
    def size_bytes(self) -> int:
        """Actual on-disk allocated bytes (not the sparse logical size).

        On Unix, ``stat.st_blocks`` is the number of 512-byte blocks
        actually allocated to the file; for sparse files (which our
        LMDB rolling chunks ARE — they're preallocated to ``max_size``
        but the OS only allocates the blocks that have been
        written), this is much smaller than ``st_size`` and is the
        correct "real disk usage" figure.

        On Windows, ``os.stat_result`` does NOT expose ``st_blocks``
        (it raises ``AttributeError``); the ``st_size`` is the
        logical (preallocated) size, which over-reports actual disk
        usage for sparse files.  We accept that on Windows the size
        is approximate (always at least as big as reality), which
        means the rolling-LMDB compaction may trigger more
        aggressively than strictly necessary.  That's safe — just
        slightly more eager than ideal.
        """
        try:
            stat = os.stat(self.path)
            blocks = getattr(stat, "st_blocks", None)
            if blocks is not None:
                # 512 is the POSIX standard block size that
                # ``st_blocks`` always reports in.
                return blocks * 512
            return stat.st_size
        except OSError:
            return 0

