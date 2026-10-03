import glob
import logging
import os
import threading
from typing import Iterator, Optional

from ._chunk import LmdbChunk

logger = logging.getLogger(__name__)


class ChunkCollection:
    """Manages an ordered list of :class:`LmdbChunk` objects.

    Chunks are sorted by ascending ``epoch_start``.  The **newest** chunk
    (``chunks[-1]``) is the *active* chunk — the one new entries are
    appended to.
    """

    def __init__(
        self,
        directory: str,
        max_size_bytes: int,
        chunk_size_bytes: int,
        **lmdb_kwargs,
    ):
        self.directory = directory
        self.max_size_bytes = max_size_bytes
        self.chunk_size_bytes = chunk_size_bytes
        self._lmdb_kwargs = lmdb_kwargs
        self._chunks: list[LmdbChunk] = []
        # Guards all reads/mutations of ``self._chunks``.  Writes happen on
        # scheduler worker threads (``RollingLMDB.put``) while maintenance
        # evictions (``delete_oldest``) run on the event-loop thread, so we
        # MUST synchronize chunk-list access to avoid closing a chunk env
        # mid-write or racing on chunk creation (audit fix).  RLock is
        # reentrant, allowing nested collection calls.
        self._lock = threading.RLock()

        os.makedirs(directory, exist_ok=True)
        self._discover()

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_epoch_from_filename(filename: str) -> Optional[int]:
        """Extract the epoch from a chunk filename like ``"123.mdb"`` or ``"123_1.mdb"``."""
        epoch_str = filename[: -len(".mdb")]
        # Strip a ``_N`` collision suffix if present.
        if "_" in epoch_str:
            epoch_str = epoch_str.split("_")[0]
        try:
            return int(epoch_str)
        except ValueError:
            return None

    def _discover(self) -> None:
        self._chunks.clear()

        pattern = os.path.join(self.directory, "*.mdb")
        for mdb_path in sorted(glob.glob(pattern)):
            filename = os.path.basename(mdb_path)
            # ``lock.mdb`` is LMDB's per-directory reader/writer lock
            # file, not a real chunk.  Skip it — it's handled by lmdb.open
            # when it opens the first chunk.
            if filename == "lock.mdb":
                continue
            epoch_start = self._parse_epoch_from_filename(filename)
            if epoch_start is None:
                continue

            chunk = LmdbChunk(
                mdb_path, epoch_start, self.chunk_size_bytes, **self._lmdb_kwargs
            )
            try:
                chunk.open()
            except Exception as exc:
                # On Windows, opening an LMDB file can fail for two
                # very different reasons that both surface as
                # MDB_IO_ERROR ("Input/output error"):
                #
                #  1. Another process has the file open (typical: the
                #     NSSM service is still running when the user starts
                #     the CLI).  The data is fine; the lock is held.
                #
                #  2. The file is in a half-written state from a previous
                #     crash and the data + lock files are corrupt.  The
                #     data may still be recoverable (Test 1 of the
                #     ``--diag`` mode opens without the lock and often
                #     succeeds) but the lock file is typically toast.
                #
                # Don't crash the whole app for one bad chunk — log the
                # failure and skip it.  The new ``active_chunk`` code
                # path will create a fresh chunk on the next write.
                import logging
                err_str = str(exc)
                # On Windows, a corrupt lock file is the most common
                # cause of Test 1 succeeding and Test 2/3 failing.
                # LMDB exposes this as "lock table contains unresolved
                # entries" or "Invalid argument" alongside the IO
                # error, depending on the version.  Either way, tell
                # the operator how to recover.
                if "lock" in err_str.lower() or "lock.mdb" in err_str.lower():
                    lock_path = os.path.join(self.directory, "lock.mdb")
                    lock_hint = (
                        f"  The LMDB lock file at\n"
                        f"    {lock_path}\n"
                        f"  is corrupt.  Your DATA (the .mdb chunks) is\n"
                        f"  almost certainly fine — only the lock file needs\n"
                        f"  to be replaced.  Stop the ParkVIS service if\n"
                        f"  it's running, then delete the lock file:\n"
                        f"    del \"{lock_path}\"\n"
                        f"  and restart.  LMDB will recreate the lock file\n"
                        f"  on the next open."
                    )
                else:
                    lock_hint = ""
                logging.getLogger(__name__).warning(
                    "Skipping LMDB chunk %s: %s.%s\n"
                    "  If this is the only instance running, the data\n"
                    "  file may be corrupt.  Run ``park-vis.exe --diag``\n"
                    "  for a three-step diagnostic of the file.\n"
                    "  If only the lock file is bad, deleting it will\n"
                    "  recover the data; otherwise stop the ParkVIS\n"
                    "  service first (NSSM / Windows Service Manager).",
                    mdb_path, exc, lock_hint,
                )
                continue
            self._chunks.append(chunk)

        self._chunks.sort(key=lambda c: c.epoch_start)

    # ------------------------------------------------------------------
    # chunk access
    # ------------------------------------------------------------------

    @property
    def active_chunk(self) -> Optional[LmdbChunk]:
        """Newest (writable) chunk, or *None* if the collection is empty."""
        with self._lock:
            return self._chunks[-1] if self._chunks else None

    def get_chunk(self, timestamp: int) -> Optional[LmdbChunk]:
        """Return the newest chunk whose ``epoch_start <= timestamp``.

        Returns *None* when *timestamp* is older than every known chunk
        and the collection is empty.
        """
        with self._lock:
            for chunk in reversed(self._chunks):
                if chunk.epoch_start <= timestamp:
                    return chunk
        return None

    # ------------------------------------------------------------------
    # chunk lifecycle
    # ------------------------------------------------------------------

    def create_chunk(self, timestamp: int) -> LmdbChunk:
        """Create a new chunk file and add it to the collection.

        The chunk is opened immediately and becomes the new active chunk.
        If the path already exists a counter suffix is appended to avoid
        collisions.
        """
        path = os.path.join(self.directory, f"{timestamp}.mdb")
        if os.path.exists(path):
            for i in range(1, 1000):
                alt = os.path.join(self.directory, f"{timestamp}_{i}.mdb")
                if not os.path.exists(alt):
                    path = alt
                    break

        chunk = LmdbChunk(
            path, timestamp, self.chunk_size_bytes, **self._lmdb_kwargs
        )
        chunk.open()
        with self._lock:
            self._chunks.append(chunk)
            self._chunks.sort(key=lambda c: c.epoch_start)
        return chunk

    def delete_oldest(self) -> None:
        """Close and remove the oldest chunk file from disk."""
        with self._lock:
            if not self._chunks:
                return
            oldest = self._chunks[0]
            oldest.close()
            try:
                os.remove(oldest.path)
            except OSError:
                pass
            # LMDB's lock is a single shared ``lock.mdb`` in the directory,
            # not a per-chunk ``<name>.mdb-lock`` file, so there is nothing
            # to remove here (the old code targeted a file that never exists).
            self._chunks.pop(0)

    def enforce_size_limit(self) -> None:
        """Delete oldest chunks until ``total_size_bytes <= max_size_bytes``.

        At least one chunk is always preserved.
        """
        while self.total_size_bytes > self.max_size_bytes and self.num_chunks > 1:
            self.delete_oldest()

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------

    @property
    def total_size_bytes(self) -> int:
        with self._lock:
            return sum(c.size_bytes for c in self._chunks)

    @property
    def num_chunks(self) -> int:
        with self._lock:
            return len(self._chunks)

    # ------------------------------------------------------------------
    # iteration
    # ------------------------------------------------------------------

    def iterate(self) -> Iterator[tuple[str, bytes]]:
        """Yield ``(key, value)`` from newest chunk to oldest."""
        with self._lock:
            chunks = list(reversed(self._chunks))
        for chunk in chunks:
            yield from chunk.iterate()


    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            for chunk in self._chunks:
                chunk.close()

    def reload(self) -> None:
        with self._lock:
            self.close()
            self._chunks.clear()
            self._discover()
