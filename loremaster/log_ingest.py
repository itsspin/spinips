"""Threaded, Tk-free ingestion of raw EverQuest log lines.

``LogIngestWorker`` owns a watcher-like object whose ``poll()`` method returns
``(lines, switched)``.  The worker serializes character-switch markers, bounded
line batches, and recoverable error statuses into one FIFO queue.  Producers
backpressure when that queue is full; records are never silently evicted.

The UI-facing ``drain()`` method is strictly nonblocking.  A Tk integration can
therefore drain one small record at a time until its frame budget is exhausted,
without ever touching the watcher or its file handle on Tk's thread.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass
from typing import Protocol, Sequence, TypeAlias


class WatcherLike(Protocol):
    """Minimal watcher contract consumed exclusively by the worker thread."""

    path: object
    character: str
    server: str

    def poll(self) -> tuple[Sequence[str], bool]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SwitchRecord:
    """The watcher selected a different log/character before later batches."""

    path: str | None
    character: str
    server: str


@dataclass(frozen=True, slots=True)
class LineBatchRecord:
    """A small ordered group of raw, decoded log lines."""

    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StatusRecord:
    """A background failure surfaced to, but never raised on, the UI thread."""

    operation: str
    error_type: str
    message: str
    recoverable: bool = True


IngestRecord: TypeAlias = SwitchRecord | LineBatchRecord | StatusRecord


class LogIngestWorker:
    """Own one watcher and publish its output without blocking the UI.

    Queue capacity is measured in records.  ``batch_lines`` also bounds each
    line record, so a short UI drain cannot accidentally inherit a complete
    multi-megabyte warm start.  While running, a full queue pauses the producer
    instead of discarding data.  Explicit shutdown is cancellation: it releases
    a producer waiting on backpressure so application exit cannot deadlock.
    """

    def __init__(
        self,
        watcher: WatcherLike,
        *,
        poll_interval: float = 0.10,
        queue_size: int = 64,
        batch_lines: int = 64,
        thread_name: str = "LoremasterLogIngest",
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than zero")
        if queue_size < 1:
            raise ValueError("queue_size must be at least one")
        if batch_lines < 1:
            raise ValueError("batch_lines must be at least one")

        self._watcher = watcher
        self._poll_interval = float(poll_interval)
        self._batch_lines = int(batch_lines)
        self._records: queue.Queue[IngestRecord] = queue.Queue(maxsize=queue_size)
        self._terminal_records: deque[StatusRecord] = deque()
        self._terminal_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=thread_name,
            daemon=True,
        )
        self._thread.start()

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def is_closed(self) -> bool:
        return self._stop.is_set()

    @property
    def pending_count(self) -> int:
        """Return an approximate count suitable for diagnostics, not locking."""

        with self._terminal_lock:
            terminal_count = len(self._terminal_records)
        return self._records.qsize() + terminal_count

    @property
    def daemon(self) -> bool:
        return self._thread.daemon

    def drain(self, max_records: int | None = None) -> list[IngestRecord]:
        """Immediately remove up to ``max_records`` records in FIFO order."""

        if max_records is not None and max_records < 0:
            raise ValueError("max_records cannot be negative")
        found: list[IngestRecord] = []
        while max_records is None or len(found) < max_records:
            try:
                found.append(self._records.get_nowait())
            except queue.Empty:
                break
        # Terminal failures happen after the producer has stopped, so they are
        # ordered behind every record it successfully placed on the main FIFO.
        if self._records.empty():
            with self._terminal_lock:
                while (self._terminal_records
                       and (max_records is None or len(found) < max_records)):
                    found.append(self._terminal_records.popleft())
        return found

    def join(self, timeout: float | None = None) -> bool:
        """Wait at most ``timeout`` seconds and report whether the worker ended."""

        if threading.current_thread() is self._thread:
            return False
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def close(self, timeout: float = 1.0) -> bool:
        """Request cancellation and defensively join the daemon worker.

        The stop event interrupts both the polling delay and queue-backpressure
        waits.  A watcher whose own ``poll()`` blocks longer than ``timeout``
        cannot hold application shutdown hostage; ``False`` reports that case,
        and the daemon thread remains safe for process exit.
        """

        if timeout < 0:
            raise ValueError("timeout cannot be negative")
        self._stop.set()
        return self.join(timeout)

    def __enter__(self) -> "LogIngestWorker":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _enqueue(self, record: IngestRecord) -> bool:
        """Losslessly enqueue while active, or abort promptly during shutdown."""

        while not self._stop.is_set():
            try:
                self._records.put(record, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _switch_record(self) -> SwitchRecord:
        path = getattr(self._watcher, "path", None)
        return SwitchRecord(
            path=None if path is None else str(path),
            character=str(getattr(self._watcher, "character", "?")),
            server=str(getattr(self._watcher, "server", "?")),
        )

    @staticmethod
    def _validated_lines(lines: Sequence[str]) -> tuple[str, ...]:
        if isinstance(lines, (str, bytes, bytearray)):
            raise TypeError("watcher.poll() lines must be a sequence of strings")
        result = tuple(lines)
        if not all(isinstance(line, str) for line in result):
            raise TypeError("watcher.poll() returned a non-string log line")
        return result

    def _publish_poll_error(self, exc: Exception) -> bool:
        return self._enqueue(StatusRecord(
            operation="poll",
            error_type=type(exc).__name__,
            message=str(exc) or repr(exc),
        ))

    def _publish_terminal_error(self, operation: str, exc: Exception) -> None:
        record = StatusRecord(
            operation=operation,
            error_type=type(exc).__name__,
            message=str(exc) or repr(exc),
            recoverable=False,
        )
        with self._terminal_lock:
            self._terminal_records.append(record)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    polled = self._watcher.poll()
                    if not isinstance(polled, tuple) or len(polled) != 2:
                        raise TypeError("watcher.poll() must return (lines, switched)")
                    raw_lines, switched = polled
                    lines = self._validated_lines(raw_lines)
                except Exception as exc:
                    if not self._publish_poll_error(exc):
                        return
                    self._stop.wait(self._poll_interval)
                    continue

                # A switch always precedes lines returned by the same poll.
                if switched and not self._enqueue(self._switch_record()):
                    return
                for start in range(0, len(lines), self._batch_lines):
                    batch = LineBatchRecord(lines[start:start + self._batch_lines])
                    if not self._enqueue(batch):
                        return
                self._stop.wait(self._poll_interval)
        except Exception as exc:
            self._publish_terminal_error("worker", exc)
        finally:
            try:
                self._watcher.close()
            except Exception as exc:
                # Keep terminal errors out of the bounded producer queue: it may
                # be full precisely because the UI has begun shutting down.
                self._publish_terminal_error("close", exc)


__all__ = [
    "IngestRecord",
    "LineBatchRecord",
    "LogIngestWorker",
    "StatusRecord",
    "SwitchRecord",
    "WatcherLike",
]
