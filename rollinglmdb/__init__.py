import os
import time
from typing import Iterator, Optional

import lmdb

from ._collection import ChunkCollection

__all__ = ["RollingLMDB"]


class RollingLMDB:
    """Time-ordered, size-rolled LMDB blob store.

    Binary blobs are indexed by ``(timestamp, key)`` — the *timestamp*
    determines which chunk the entry lands in and the *key* identifies
    it within that chunk.

    When the total apparent size of all chunk files exceeds
    *max_size_gb* the oldest chunk is removed, giving a first-in-first-
    out eviction policy.

    .. caution::

       *timestamps* **must** be monotonically non-decreasing.  Writing
       an entry whose timestamp is older than the active chunk's start
       epoch will make that entry unreachable via :meth:`get` because
       the lookup algorithm searches the chunk whose ``epoch_start`` is
       the closest value **≤** the requested timestamp.

    Parameters
    ----------
    directory : str
        Path to the directory that will hold the ``.mdb`` chunk files.
        Created automatically if it does not exist.
    max_size_gb : float
        Rolling size limit in gigabytes.  When the sum of all chunk
        logical sizes exceeds this value the oldest chunk file is
        deleted.
    chunk_size_mb : int
        Logical map size of each chunk file in megabytes.
        Default: 1000 (1 GB).
    readonly : bool
        If *True*, :meth:`put` raises ``RuntimeError``.
    \\*\\*lmdb_kwargs
        Passed through to :func:`lmdb.open` for each chunk.  Defaults
        are tuned for write-heavy workloads on Linux with sparse files:
        ``writemap=True, map_async=True, metasync=False, sync=False,
        readahead=False``.
    """

    def __init__(
        self,
        directory: str,
        max_size_gb: float = 10.0,
        chunk_size_mb: int = 1000,
        readonly: bool = False,
        **lmdb_kwargs: dict,
    ):
        self.directory = directory
        self.max_size_gb = max_size_gb
        self.chunk_size_mb = chunk_size_mb
        self.readonly = readonly

        max_size_bytes = int(max_size_gb * 1024 * 1024 * 1024)
        chunk_size_bytes = chunk_size_mb * 1024 * 1024
        if max_size_bytes < chunk_size_bytes:
            max_size_bytes = chunk_size_bytes

        self._collection = ChunkCollection(
            directory=directory,
            max_size_bytes=max_size_bytes,
            chunk_size_bytes=chunk_size_bytes,
            **lmdb_kwargs,
        )
        self._closed = False

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def put(self, key: str, value: bytes, timestamp: int) -> None:
        """Store a blob.

        The entry is written to the **active chunk** (the newest one).
        The *timestamp* is used for collision avoidance only — if the
        active chunk is full a new chunk is created whose ``epoch_start
        >= timestamp``.  For retrieval via :meth:`get`, the *timestamp*
        selects the correct chunk (the newest chunk whose
        ``epoch_start <= timestamp``).

        Parameters
        ----------
        key : str
            Opaque identifier (e.g. ``str(scan_id)``).
        value : bytes
            Binary payload (e.g. JPEG bytes).
        timestamp : int
            Epoch milliseconds.  Used for chunk placement and retrieval.

        Raises
        ------
        RuntimeError
            If the database was opened in read-only mode or has been
            closed.
        """
        if self.readonly:
            raise RuntimeError("Database is read-only")
        if self._closed:
            raise RuntimeError("Database has been closed")
        if not key:
            raise ValueError("key must not be empty")

        chunk = self._collection.active_chunk
        if chunk is None:
            chunk = self._collection.create_chunk(timestamp)

        for _ in range(2):
            try:
                chunk.put(key, value, timestamp=timestamp)
                break
            except lmdb.MapFullError:
                chunk = self._collection.create_chunk(
                    max(timestamp, chunk.epoch_start + 1)
                )
        else:
            raise RuntimeError(
                f"value ({len(value)} bytes) exceeds chunk map size "
                f"({self.chunk_size_mb} MB)"
            )

        self._collection.enforce_size_limit()

    def get(self, key: str, timestamp: int) -> Optional[bytes]:
        """Retrieve a blob by key and timestamp.

        Parameters
        ----------
        key : str
            The same opaque identifier used in :meth:`put`.
        timestamp : int
            Epoch milliseconds.  Determines which chunk is searched.

        Returns
        -------
        bytes or None
            The stored value, or *None* if no matching entry was found.
        """
        if self._closed:
            raise RuntimeError("Database has been closed")
        chunk = self._collection.get_chunk(timestamp)
        if chunk is None:
            return None
        return chunk.get(key)

    def iterate(self) -> Iterator[tuple[str, bytes]]:
        """Yield ``(key, value)`` pairs from newest chunk to oldest."""
        if self._closed:
            raise RuntimeError("Database has been closed")
        return self._collection.iterate()

    def delete_oldest(self) -> None:
        """Delete the oldest chunk file.

        This is a no-op if only one chunk remains (the active chunk is
        always preserved).  Use it from maintenance code to evict chunks
        whose entries have all passed their retention window.
        """
        self._collection.delete_oldest()

    def close(self) -> None:
        """Close all chunk environments.

        The instance must not be used after calling this method unless
        :meth:`reload` is called first.
        """
        self._collection.close()
        self._closed = True

    def reload(self) -> None:
        """Re-scan the directory for chunk files.

        Use this when external processes have added or removed ``.mdb``
        files.
        """
        self._collection.reload()
        self._closed = False

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @property
    def num_chunks(self) -> int:
        """Number of chunk files on disk."""
        return self._collection.num_chunks

    @property
    def total_size_bytes(self) -> int:
        """Sum of the logical sizes of all chunk files."""
        return self._collection.total_size_bytes

    @property
    def total_entries(self) -> int:
        """Total number of entries across all chunks."""
        return sum(c.num_entries for c in self._collection._chunks)

    @property
    def max_size_bytes(self) -> int:
        """Configured rolling size limit in bytes."""
        return self._collection.max_size_bytes

    @property
    def chunk_size_bytes(self) -> int:
        """Configured per-chunk map size in bytes."""
        return self._collection.chunk_size_bytes

    @property
    def chunks(self) -> list[dict]:
        """Detailed info for each chunk, oldest first."""
        return [
            {
                "path": c.path,
                "filename": os.path.basename(c.path),
                "epoch_start": c.epoch_start,
                "last_event_time": c.last_event_time,
                "num_entries": c.num_entries,
                "size_bytes": c.size_bytes,
                "map_size_bytes": c._map_size,
            }
            for c in self._collection._chunks
        ]


    # ------------------------------------------------------------------
    # key listing
    # ------------------------------------------------------------------

    def list_keys(self, chunk_index: int = -1) -> list[str]:
        """List all keys in a given chunk by index (default: newest).

        Returns ``[str, ...]`` — keys only, no values loaded, so this
        is fast even for chunks with many large blobs.
        """
        if not self._collection._chunks:
            return []
        chunk = self._collection._chunks[chunk_index]
        return list(chunk.iterkeys())

    def list_keys_page(self, chunk_index: int, offset: int, limit: int) -> tuple[list[dict], int]:
        """Return a page of keys with sizes and the total entry count.

        Returns ``(entries, total_count)`` where *entries* is a list of
        ``{"key": str, "size_bytes": int}`` for the requested window.
        """
        if not self._collection._chunks:
            return [], 0
        chunk = self._collection._chunks[chunk_index]
        total = chunk.num_entries
        entries = [
            {"key": k, "size_bytes": sz}
            for k, sz in chunk.iterkeys_page(offset, limit)
        ]
        return entries, total

    # ------------------------------------------------------------------
    # context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "RollingLMDB":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
