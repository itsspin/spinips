import dataclasses
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))

import hover_ocr  # noqa: E402
from hover_ocr import (  # noqa: E402
    CaptureMetadata,
    HoverCapture,
    HoverOcrService,
    OcrLine,
    cleanup_stale_scan_dirs,
    rank_item_candidates,
)


def make_capture(cursor=(123, 234), marker=17):
    width, height = 4, 3
    stride = (width * 3 + 3) & ~3
    pixels = bytearray(stride * height)
    for index in range(0, len(pixels), 3):
        pixels[index:index + 3] = bytes((marker, marker + 20, marker + 40))
    metadata = CaptureMetadata(
        cursor_x=cursor[0], cursor_y=cursor[1],
        region_left=cursor[0] - 2, region_top=cursor[1] - 1,
        region_width=width, region_height=height,
        foreground_hwnd=0x123456, foreground_pid=4321,
        captured_at=1000.0,
    )
    return HoverCapture(
        metadata=metadata,
        bmp_bytes=hover_ocr._bmp_from_bgr(bytes(pixels), width, height, stride),
        luminance_range=20,
    )


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

    def test_legends_bracer_title_beats_wrapped_classes_and_upgrade_controls(self):
        lines = [
            OcrLine("Pristine Studded Leather Bracer +3", 118, 2, 192, 10),
            OcrLine("Description", 177, 24, 63, 12),
            OcrLine("Pristine Studded Leather Bracer +3", 69, 54, 192, 10),
            OcrLine("No Trade", 69, 70, 51, 10),
            OcrLine("Class: WAR CLR PAL RNG SHD DRU MNK BRD ROG", 69, 86, 296, 10),
            OcrLine("SHM BST BER", 69, 102, 80, 10),
            OcrLine("Wrist", 69, 118, 30, 10),
            OcrLine("Tier 3", 124, 159, 31, 10),
            OcrLine("Merge Place", 28, 164, 84, 12),
        ]
        candidates = rank_item_candidates(lines, cursor_x=58, cursor_y=94)
        self.assertEqual(candidates[0], "Pristine Studded Leather Bracer")
        for noise in ("No Trade", "SHM BST BER", "Wrist", "Tier 3", "Merge Place"):
            self.assertNotIn(noise, candidates)


class CaptureArtifactTests(unittest.TestCase):
    def test_capture_and_metadata_are_immutable_and_local_cursor_is_exact(self):
        capture = make_capture(cursor=(-120, 845))
        self.assertIsInstance(capture.bmp_bytes, bytes)
        self.assertEqual(capture.metadata.cursor_in_region, (2.0, 1.0))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            capture.metadata.cursor_x = 0
        with self.assertRaises(dataclasses.FrozenInstanceError):
            capture.bmp_bytes = b"changed"

    def test_top_down_24bit_bmp_header_and_size(self):
        width, height = 3, 2
        stride = (width * 3 + 3) & ~3
        pixels = bytes(range(stride * height))
        bmp = hover_ocr._bmp_from_bgr(pixels, width, height, stride)
        signature, file_size, _one, _two, offset = struct.unpack("<2sIHHI", bmp[:14])
        header = struct.unpack("<IiiHHIIiiII", bmp[14:54])
        self.assertEqual(signature, b"BM")
        self.assertEqual(file_size, len(bmp))
        self.assertEqual(offset, 54)
        self.assertEqual(header[1:5], (width, -height, 1, 24))
        self.assertEqual(bmp[54:], pixels)

    def test_blank_frame_detection_is_conservative(self):
        width, height, stride = 8, 4, 24
        blank = bytes(stride * height)
        varied = bytearray(blank)
        varied[0:3] = bytes((0, 255, 255))
        self.assertEqual(hover_ocr._luminance_range(blank, width, height, stride), 0)
        self.assertGreaterEqual(
            hover_ocr._luminance_range(bytes(varied), width, height, stride), 6)

    def test_powershell_only_decodes_scales_and_runs_winrt_ocr(self):
        script = hover_ocr._powershell_script()
        self.assertIn("Windows.Media.Ocr.OcrEngine", script)
        self.assertIn("BitmapTransform", script)
        self.assertIn("ScaledWidth", script)
        self.assertIn("SPIN_LOREMASTER_OCR_PATH", script)
        self.assertNotIn("CopyFromScreen", script)
        self.assertNotIn("BitBlt", script)
        self.assertNotIn("GetForegroundWindow", script)
        self.assertNotIn("Add-Type", script)

    def test_cleanup_removes_only_known_stale_scan_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / hover_ocr.TEMP_ROOT_NAME
            root.mkdir()
            old = root / ("scan-" + "a" * 32)
            recent = root / ("scan-" + "b" * 32)
            unrelated = root / "do-not-touch"
            for directory in (old, recent, unrelated):
                directory.mkdir()
            (old / "cursor-region.bmp").write_bytes(b"old")
            (recent / "cursor-region.bmp").write_bytes(b"recent")
            now = time.time()
            os.utime(old, (now - 1000, now - 1000))
            os.utime(recent, (now, now))
            removed = cleanup_stale_scan_dirs(root, now=now, max_age=100)
            self.assertEqual(removed, 1)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())


class HoverOcrServiceTests(unittest.TestCase):
    def test_capture_is_synchronous_and_worker_receives_same_immutable_object(self):
        capture = make_capture()
        caller_thread = threading.get_ident()
        capture_calls = []
        worker_calls = []

        def capture_factory():
            capture_calls.append(threading.get_ident())
            return capture

        def scanner(received, _cancel):
            worker_calls.append((threading.get_ident(), received))
            return ["Cloak of Flames"], []

        service = HoverOcrService(scanner, capture_factory)
        self.addCleanup(service.close)
        request_id = service.submit()
        self.assertEqual(capture_calls, [caller_thread])
        deadline = time.monotonic() + 1.0
        results = []
        while time.monotonic() < deadline and not results:
            results = service.poll()
            time.sleep(0.005)
        self.assertIs(worker_calls[0][1], capture)
        self.assertNotEqual(worker_calls[0][0], caller_thread)
        self.assertEqual(results[0].request_id, request_id)
        self.assertEqual(results[0].capture, capture.metadata)

    def test_new_request_cancels_active_ocr_and_latest_result_wins(self):
        first = make_capture(cursor=(1, 1), marker=10)
        latest = make_capture(cursor=(2, 2), marker=20)
        first_started = threading.Event()

        def scanner(capture, cancel):
            if capture is first:
                first_started.set()
                while not cancel.wait(0.005):
                    pass
                raise RuntimeError("Hover scan cancelled.")
            return ["Cloak of Flames"], []

        service = HoverOcrService(scanner, lambda: first)
        self.addCleanup(service.close)
        first_id = service.submit(first)
        self.assertTrue(first_started.wait(0.5))
        latest_id = service.submit(latest)
        deadline = time.monotonic() + 1.0
        results = []
        while time.monotonic() < deadline:
            results.extend(service.poll())
            if any(row.request_id == latest_id for row in results):
                break
            time.sleep(0.005)
        newest = next(row for row in results if row.request_id == latest_id)
        self.assertEqual(newest.candidates, ["Cloak of Flames"])
        self.assertEqual(newest.capture.cursor_x, 2)
        self.assertTrue(any(row.request_id == first_id and row.error for row in results))

    def test_capture_failure_is_reported_without_starting_worker_ocr(self):
        scanner_called = threading.Event()

        def capture_factory():
            raise hover_ocr.HoverCaptureError("Hover scan only captures EverQuest.")

        def scanner(_capture, _cancel):
            scanner_called.set()
            return [], []

        service = HoverOcrService(scanner, capture_factory)
        self.addCleanup(service.close)
        request_id = service.submit()
        results = service.poll()
        self.assertEqual(results[0].request_id, request_id)
        self.assertIn("EverQuest", results[0].error)
        self.assertFalse(scanner_called.is_set())

    def test_close_cancels_worker_and_submit_after_close_is_rejected(self):
        capture = make_capture()
        started = threading.Event()
        stopped = threading.Event()

        def scanner(_capture, cancel):
            started.set()
            cancel.wait(2.0)
            stopped.set()
            raise RuntimeError("cancelled")

        service = HoverOcrService(scanner, lambda: capture)
        service.submit(capture)
        self.assertTrue(started.wait(0.5))
        service.close()
        self.assertTrue(stopped.wait(0.2))
        with self.assertRaises(RuntimeError):
            service.submit(capture)


if __name__ == "__main__":
    unittest.main()
