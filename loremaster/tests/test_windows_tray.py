import sys
import unittest
from pathlib import Path
from unittest import mock


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))

import windows_tray  # noqa: E402
from windows_tray import (  # noqa: E402
    TRAY_EXIT,
    TRAY_HIDE,
    TRAY_SHOW,
    TrayCommandQueue,
    WindowsTrayIcon,
    build_brand_icon_planes,
    overlay_should_be_visible,
)


class OverlayVisibilityTests(unittest.TestCase):
    def test_runtime_tray_hide_always_wins(self):
        for wait_for_eq in (False, True):
            for eq_running in (False, True):
                for manual_show in (False, True):
                    self.assertFalse(overlay_should_be_visible(
                        hidden_to_tray=True,
                        wait_for_eq=wait_for_eq,
                        eq_running=eq_running,
                        manual_show=manual_show,
                    ))

    def test_wait_mode_auto_hides_but_explicit_tray_restore_can_override(self):
        self.assertFalse(overlay_should_be_visible(
            hidden_to_tray=False, wait_for_eq=True,
            eq_running=False, manual_show=False))
        self.assertTrue(overlay_should_be_visible(
            hidden_to_tray=False, wait_for_eq=True,
            eq_running=False, manual_show=True))
        self.assertTrue(overlay_should_be_visible(
            hidden_to_tray=False, wait_for_eq=True,
            eq_running=True, manual_show=False))

    def test_normal_launch_is_visible_without_eq_process_probe(self):
        self.assertTrue(overlay_should_be_visible(
            hidden_to_tray=False, wait_for_eq=False,
            eq_running=False, manual_show=False))


class TrayCommandTests(unittest.TestCase):
    def test_commands_cross_the_thread_boundary_in_order(self):
        commands = TrayCommandQueue()
        self.assertTrue(commands.emit(TRAY_SHOW))
        self.assertTrue(commands.emit(TRAY_HIDE))
        self.assertEqual(commands.drain(), [TRAY_SHOW, TRAY_HIDE])

    def test_exit_is_dispatched_once_and_rejects_late_commands(self):
        commands = TrayCommandQueue()
        self.assertTrue(commands.emit(TRAY_EXIT))
        self.assertFalse(commands.emit(TRAY_EXIT))
        self.assertFalse(commands.emit(TRAY_SHOW))
        self.assertEqual(commands.drain(), [TRAY_EXIT])

    def test_unknown_commands_are_rejected(self):
        commands = TrayCommandQueue()
        self.assertFalse(commands.emit("restart"))
        self.assertEqual(commands.drain(), [])


class TrayIconTests(unittest.TestCase):
    def test_generated_brand_icon_has_crisp_transparency_and_center_mark(self):
        pixels, mask, mask_stride = build_brand_icon_planes(32)
        self.assertEqual(len(pixels), 32 * 32 * 4)
        self.assertEqual(len(mask), mask_stride * 32)
        self.assertEqual(mask_stride, 4)
        self.assertEqual(pixels[3], 0)  # transparent outside the hex
        center = (16 * 32 + 16) * 4
        self.assertEqual(pixels[center + 3], 255)
        self.assertEqual(mask[16 * mask_stride + 16 // 8]
                         & (0x80 >> (16 % 8)), 0)

    def test_invalid_icon_sizes_fail_cleanly(self):
        for size in (0, 15, 65, 256):
            with self.subTest(size=size), self.assertRaises(ValueError):
                build_brand_icon_planes(size)

    def test_non_windows_start_and_repeated_close_are_noops(self):
        tray = WindowsTrayIcon()
        with mock.patch.object(windows_tray.os, "name", "posix"):
            self.assertFalse(tray.start())
            tray.close()
            tray.close()
        self.assertFalse(tray.active)
        self.assertEqual(tray.poll(), [])


if __name__ == "__main__":
    unittest.main()
