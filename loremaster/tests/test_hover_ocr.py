import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))

import hover_ocr  # noqa: E402
from hover_ocr import HoverOcrService, OcrLine, rank_item_candidates  # noqa: E402


class HoverCandidateTests(unittest.TestCase):
    def test_item_title_outranks_stats_and_ui_noise(self):
        lines = [
            OcrLine("Inventory", 10, 10, 70, 12),
            OcrLine("Description", 430, 90, 100, 12),
            OcrLine("Cloak of Flames +4", 445, 116, 160, 16),
            OcrLine("MAGIC ITEM NO DROP", 445, 142, 170, 12),
            OcrLine("AC: 10  DEX: +9", 445, 165, 150, 12),
            OcrLine("Cloak of Flames", 448, 119, 150, 15),
            OcrLine("Ancient Silken Robe", 60, 620, 180, 18),
        ]
        candidates = rank_item_candidates(lines, cursor_x=470, cursor_y=130)
        self.assertEqual(candidates[0], "Cloak of Flames")
        self.assertNotIn("Description", candidates)
        self.assertNotIn("Inventory", candidates)

    def test_zero_candidate_limit_is_respected(self):
        self.assertEqual(rank_item_candidates([OcrLine("Cloak of Flames")], limit=0), [])

    def test_powershell_capture_is_eq_only_bounded_and_winrt_ocr(self):
        script = hover_ocr._powershell_script()
        self.assertIn("Windows.Media.Ocr.OcrEngine", script)
        self.assertIn("Hover scan only captures EverQuest", script)
        self.assertIn("GetGenericArguments().Count -eq 1", script)
        self.assertIn("$lines.Count -ge 200", script)
        self.assertIn(str(hover_ocr.ROI_WIDTH), script)
        self.assertIn(str(hover_ocr.ROI_HEIGHT), script)


class HoverOcrServiceTests(unittest.TestCase):
    def test_cursor_is_snapshotted_before_worker_and_forwarded(self):
        seen = []

        def scanner(cursor, _cancel):
            seen.append(cursor)
            return ["Cloak of Flames"], []

        with patch.object(hover_ocr, "get_cursor_position", return_value=(123, -45)):
            service = HoverOcrService(scanner)
            self.addCleanup(service.close)
            request_id = service.submit()
        deadline = time.monotonic() + 1.0
        results = []
        while time.monotonic() < deadline and not results:
            results = service.poll()
            time.sleep(0.005)
        self.assertEqual(seen, [(123, -45)])
        self.assertEqual(results[0].request_id, request_id)

    def test_new_request_cancels_active_scan_and_latest_result_wins(self):
        first_started = threading.Event()

        def scanner(cursor, cancel):
            if cursor == (1, 1):
                first_started.set()
                while not cancel.wait(0.005):
                    pass
                raise RuntimeError("Hover scan cancelled.")
            return ["Cloak of Flames"], []

        service = HoverOcrService(scanner)
        self.addCleanup(service.close)
        first_id = service.submit((1, 1))
        self.assertTrue(first_started.wait(0.5))
        latest_id = service.submit((2, 2))
        deadline = time.monotonic() + 1.0
        results = []
        while time.monotonic() < deadline:
            results.extend(service.poll())
            if any(row.request_id == latest_id for row in results):
                break
            time.sleep(0.005)
        latest = next(row for row in results if row.request_id == latest_id)
        self.assertEqual(latest.candidates, ["Cloak of Flames"])
        self.assertTrue(any(row.request_id == first_id and row.error for row in results))

    def test_close_cancels_worker_and_submit_after_close_is_rejected(self):
        started = threading.Event()
        stopped = threading.Event()

        def scanner(_cursor, cancel):
            started.set()
            cancel.wait(2.0)
            stopped.set()
            raise RuntimeError("cancelled")

        service = HoverOcrService(scanner)
        service.submit((1, 1))
        self.assertTrue(started.wait(0.5))
        service.close()
        self.assertTrue(stopped.wait(0.2))
        with self.assertRaises(RuntimeError):
            service.submit((2, 2))


if __name__ == "__main__":
    unittest.main()
