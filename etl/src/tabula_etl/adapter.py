"""Adapter — the source-side contract.

A consumer subclasses Adapter to bring a specific data source (Telegram,
Kitsu, Slack, calendar, file system) into the framework. The framework
handles scheduling and batching; the adapter handles "go fetch new events
since timestamp X."
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from tabula_etl.types import RawEvent


class Adapter(ABC):
    """The contract for an L2 source adapter.

    Lifecycle: instantiate with config → call fetch() repeatedly → the framework
    tracks the high-water mark via the idempotency store.
    """

    # A stable name used for audit + state-tracking. e.g. "telegram-botho-coord".
    name: str

    @abstractmethod
    async def fetch(self, *, since: datetime | None = None) -> AsyncIterator[RawEvent]:
        """Yield events that occurred at or after `since`.

        If `since` is None, the adapter should yield from its earliest available
        event (typically used for backfills). Implementations should be resumable —
        crashing mid-stream and re-running with the same `since` should produce
        the same logical events (the framework will dedup by `source_id`).
        """
        # Force this to be an async generator at type level.
        if False:  # type: ignore[unreachable]
            yield  # pragma: no cover

    async def close(self) -> None:
        """Release any source-side resources (HTTP clients, DB connections)."""
        return None


class FileAdapter(Adapter):
    """Reference adapter: walks a directory of files and yields each as a RawEvent.

    Intended for one-time backfills — e.g. "import this Signal export folder."
    Production adapters should subclass Adapter directly and use streaming APIs.
    """

    name = "file"

    def __init__(self, path: str, *, glob: str = "*", source_url_prefix: str = "file://") -> None:
        self.path = path
        self.glob = glob
        self.source_url_prefix = source_url_prefix

    async def fetch(self, *, since: datetime | None = None) -> AsyncIterator[RawEvent]:
        import os
        from pathlib import Path

        root = Path(self.path)
        for p in sorted(root.glob(self.glob)):
            if not p.is_file():
                continue
            stat = p.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=_utc())
            if since and mtime < since:
                continue
            yield RawEvent(
                source_id=f"{p.relative_to(root)}@{stat.st_mtime}",
                source=f"{self.source_url_prefix}{p.relative_to(root)}",
                occurred_at=mtime,
                payload={"path": str(p), "size": stat.st_size, "content": p.read_text(errors="replace")},
            )


def _utc():  # tiny helper to avoid timezone import shuffle
    from datetime import timezone
    return timezone.utc
