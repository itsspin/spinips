import collections
import threading
import time
import sys
import unittest
from pathlib import Path


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))

from log_ingest import (  # noqa: E402
    LineBatchRecord,
    LogIngestWorker,
    StatusRecord,
    SwitchRecord,
)


class SequenceWatcher:
    def __init__(self, steps, *, character="Spin", server="Legends"):
        self.steps = collections.deque(steps)
        self.path = Path("Logs") / f"eqlog_{character}_{server}.txt"
        self.character = character
        self.server = server
        self.closed = threading.Event()

    def poll(self):
        if self.steps:
            step = self.steps.popleft()
            if isinstance(step, Exception):
                raise step
            return step
        return [], False

    def close(self):
        self.closed.set()


def collect(worker, count, timeout=1.0):
    records = []
    deadline = time.monotonic() + timeout
    while len(records) < count and time.monotonic() < deadline:
        records.extend(worker.drain(count - len(records)))
        if len(records) < count:
            time.sleep(0.002)
    return records


class LogIngestWorkerTests(unittest.TestCase):
    def test_switch_precedes_bounded_batches_without_dropping_lines(self):
        expected = [f"line-{index}" for index in range(7)]
        watcher = SequenceWatcher([(expected, True)])
        worker = LogIngestWorker(
            watcher, poll_interval=0.002, queue_size=1, batch_lines=2)
        self.addCleanup(worker.close)

        records = collect(worker, 5)
        self.assertEqual(len(records), 5)
        self.assertIsInstance(records[0], SwitchRecord)
        self.assertEqual(records[0].character, "Spin")
        self.assertEqual(records[0].server, "Legends")
        self.assertTrue(records[0].path.endswith("eqlog_Spin_Legends.txt"))
        batches = [record for record in records if isinstance(record, LineBatchRecord)]
        self.assertEqual([len(record.lines) for record in batches], [2, 2, 2, 1])
        self.assertEqual([line for record in batches for line in record.lines], expected)

    def test_sustained_backpressure_preserves_every_line_in_order(self):
        expected = [f"event-{index:04d}" for index in range(2000)]
        watcher = SequenceWatcher([(expected, False)])
        worker = LogIngestWorker(
            watcher, poll_interval=0.002, queue_size=2, batch_lines=17)
        self.addCleanup(worker.close)

        batch_count = (len(expected) + 16) // 17
        records = collect(worker, batch_count, timeout=2.0)
        self.assertEqual(len(records), batch_count)
        self.assertTrue(all(isinstance(record, LineBatchRecord) for record in records))
        self.assertEqual([line for record in records for line in record.lines], expected)

    def test_poll_exception_is_ordered_status_and_worker_recovers(self):
        watcher = SequenceWatcher([
            RuntimeError("temporary read failure"),
            (["recovered"], False),
        ])
        worker = LogIngestWorker(watcher, poll_interval=0.002)
        self.addCleanup(worker.close)

        records = collect(worker, 2)
        self.assertEqual(len(records), 2)
        self.assertIsInstance(records[0], StatusRecord)
        self.assertEqual(records[0].operation, "poll")
        self.assertEqual(records[0].error_type, "RuntimeError")
        self.assertIn("temporary read failure", records[0].message)
        self.assertEqual(records[1], LineBatchRecord(("recovered",)))

    def test_drain_is_nonblocking_and_respects_limit(self):
        watcher = SequenceWatcher([(["a", "b", "c"], False)])
        worker = LogIngestWorker(
            watcher, poll_interval=0.002, queue_size=4, batch_lines=1)
        self.addCleanup(worker.close)

        records = collect(worker, 3)
        self.assertEqual([record.lines[0] for record in records], ["a", "b", "c"])
        started = time.monotonic()
        self.assertEqual(worker.drain(max_records=1), [])
        self.assertLess(time.monotonic() - started, 0.05)
        with self.assertRaises(ValueError):
            worker.drain(max_records=-1)

    def test_close_releases_a_producer_blocked_by_backpressure(self):
        watcher = SequenceWatcher([(["one", "two", "three"], True)])
        worker = LogIngestWorker(
            watcher, poll_interval=0.002, queue_size=1, batch_lines=1)

        deadline = time.monotonic() + 0.5
        while worker.pending_count < 1 and time.monotonic() < deadline:
            time.sleep(0.002)
        started = time.monotonic()
        self.assertTrue(worker.close(timeout=0.5))
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(watcher.closed.wait(0.1))
        self.assertFalse(worker.is_alive)
        self.assertTrue(worker.is_closed)
        self.assertTrue(worker.daemon)
        self.assertTrue(worker.close(timeout=0.1))  # idempotent

    def test_invalid_watcher_payload_is_reported_not_raised(self):
        watcher = SequenceWatcher([("not-a-line-list", False)])
        worker = LogIngestWorker(watcher, poll_interval=0.002)
        self.addCleanup(worker.close)

        records = collect(worker, 1)
        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], StatusRecord)
        self.assertEqual(records[0].error_type, "TypeError")
        self.assertIn("sequence of strings", records[0].message)

    def test_close_exception_is_preserved_as_terminal_status(self):
        class BadCloseWatcher(SequenceWatcher):
            def close(self):
                super().close()
                raise OSError("close failed")

        watcher = BadCloseWatcher([])
        worker = LogIngestWorker(watcher, poll_interval=0.002)
        self.assertTrue(worker.close(timeout=0.5))
        records = worker.drain()
        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], StatusRecord)
        self.assertEqual(records[0].operation, "close")
        self.assertEqual(records[0].error_type, "OSError")
        self.assertFalse(records[0].recoverable)

    def test_close_timeout_never_waits_forever_on_a_blocked_watcher(self):
        release = threading.Event()

        class BlockingWatcher(SequenceWatcher):
            def poll(self):
                release.wait(1.0)
                return [], False

        watcher = BlockingWatcher([])
        worker = LogIngestWorker(watcher, poll_interval=0.002)
        started = time.monotonic()
        self.assertFalse(worker.close(timeout=0.01))
        self.assertLess(time.monotonic() - started, 0.10)
        release.set()
        self.assertTrue(worker.join(timeout=0.5))
        self.assertTrue(watcher.closed.is_set())


if __name__ == "__main__":
    unittest.main()
