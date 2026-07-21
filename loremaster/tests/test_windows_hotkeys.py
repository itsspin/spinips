import ctypes
import importlib.util
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))

import windows_hotkeys  # noqa: E402
from windows_hotkeys import (  # noqa: E402
    HOTKEY_RECOVERY,
    HOTKEY_WIKI,
    WM_HOTKEY,
    HotkeyBinding,
    HotkeyCommandQueue,
    WindowsHotkeyService,
)

LOREMASTER_SPEC = importlib.util.spec_from_file_location(
    "loremaster_hotkey_test_app", LOREMASTER_DIR / "loremaster.py")
loremaster_app = importlib.util.module_from_spec(LOREMASTER_SPEC)
sys.modules[LOREMASTER_SPEC.name] = loremaster_app
LOREMASTER_SPEC.loader.exec_module(loremaster_app)


def binding(command, identifier, modifiers, virtual_key, label):
    return HotkeyBinding(command, identifier, modifiers, virtual_key, label)


class HotkeyQueueTests(unittest.TestCase):
    def test_only_known_commands_cross_the_thread_boundary(self):
        commands = HotkeyCommandQueue()
        self.assertTrue(commands.emit(HOTKEY_WIKI))
        self.assertTrue(commands.emit(HOTKEY_RECOVERY))
        self.assertFalse(commands.emit("quit"))
        self.assertEqual(commands.drain(), [HOTKEY_WIKI, HOTKEY_RECOVERY])

    def test_invalid_or_colliding_binding_metadata_is_rejected(self):
        with self.assertRaises(ValueError):
            binding("unknown", 1, 0x4002, ord("E"), "Ctrl+E")
        recovery = binding(HOTKEY_RECOVERY, 7, 0x4003, ord("L"), "Ctrl+Alt+L")
        wiki = binding(HOTKEY_WIKI, 7, 0x4006, ord("E"), "Ctrl+Shift+E")
        with self.assertRaises(ValueError):
            WindowsHotkeyService(recovery, wiki)

    def test_non_windows_service_fails_cleanly_and_keeps_status_actionable(self):
        service = WindowsHotkeyService(
            binding(HOTKEY_RECOVERY, 11, 0x4003, ord("L"), "Ctrl+Alt+L"),
            binding(HOTKEY_WIKI, 12, 0x4006, ord("E"), "Ctrl+Shift+E"),
        )
        with mock.patch.object(windows_hotkeys.os, "name", "posix"):
            self.assertFalse(service.start())
        status = service.status(HOTKEY_WIKI)
        self.assertFalse(status.registered)
        self.assertIn("Windows", status.error)

    def test_tk_main_thread_never_competes_for_native_hotkey_messages(self):
        app_source = (LOREMASTER_DIR / "loremaster.py").read_text(encoding="utf-8")
        owner_source = (LOREMASTER_DIR / "windows_hotkeys.py").read_text(
            encoding="utf-8")
        self.assertNotIn("PeekMessageW", app_source)
        self.assertNotIn("RegisterHotKey(", app_source)
        self.assertIn("GetMessageW", owner_source)
        self.assertIn("CreateWindowExW", owner_source)


class MiniHudPackingTests(unittest.TestCase):
    def test_full_progression_is_kept_when_all_four_cards_fit(self):
        keys = ["combat", "kills", "money", "progress"]
        labels = loremaster_app.mini_stat_label_plan(
            keys, 450,
            {"combat": 80, "kills": 55, "money": 75, "progress": 120},
            {"progress": 65},
        )
        self.assertEqual(labels["progress"], "PROGRESSION")

    def test_720px_budget_uses_whole_xp_label_instead_of_midword_clip(self):
        keys = ["combat", "kills", "money", "progress"]
        labels = loremaster_app.mini_stat_label_plan(
            keys, 350,
            {"combat": 80, "kills": 55, "money": 75, "progress": 120},
            {"progress": 55},
        )
        self.assertEqual(labels["progress"], "XP")
        self.assertNotIn(labels["progress"], ("GRES", "PROGRES", "PROGRESS"))


@unittest.skipUnless(os.name == "nt", "native hotkey smoke test requires Windows")
class NativeHotkeySmokeTests(unittest.TestCase):
    def test_hidden_owner_receives_hotkey_and_failed_rebind_restores_old_key(self):
        # Ctrl+Alt+Shift+F23/F24 are deliberately obscure so this smoke test is
        # deterministic on developer machines and Windows CI runners.
        modifiers = 0x4000 | 0x0001 | 0x0002 | 0x0004
        recovery = binding(
            HOTKEY_RECOVERY, 0x5311, modifiers, 0x86,
            "Ctrl+Alt+Shift+F23")
        wiki = binding(
            HOTKEY_WIKI, 0x5312, modifiers, 0x87,
            "Ctrl+Alt+Shift+F24")
        service = WindowsHotkeyService(recovery, wiki)
        self.addCleanup(service.close)
        self.assertTrue(service.start(timeout=1.5), service.error)
        self.assertTrue(service.status(HOTKEY_RECOVERY).registered)
        self.assertTrue(service.status(HOTKEY_WIKI).registered)

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.assertTrue(user32.PostMessageW(
            service.window_handle, WM_HOTKEY, wiki.identifier, 0))
        deadline = time.monotonic() + 0.5
        commands = []
        while time.monotonic() < deadline and not commands:
            commands = service.poll()
            time.sleep(0.005)
        self.assertEqual(commands, [HOTKEY_WIKI])

        conflict = service.rebind_wiki(
            recovery.modifiers, recovery.virtual_key,
            recovery.label, timeout=1.0)
        self.assertFalse(conflict.success)
        self.assertIn("already in use", conflict.status.error)
        restored = service.status(HOTKEY_WIKI)
        self.assertTrue(restored.registered)
        self.assertEqual(restored.binding, wiki)

        rebound = service.rebind_wiki(
            modifiers, 0x85, "Ctrl+Alt+Shift+F22", timeout=1.0)
        self.assertTrue(rebound.success, rebound.status.error)
        self.assertTrue(service.status(HOTKEY_WIKI).registered)
        self.assertEqual(
            service.status(HOTKEY_WIKI).binding.label,
            "Ctrl+Alt+Shift+F22")

        service.close(timeout=1.0)
        self.assertFalse(service.active)


if __name__ == "__main__":
    unittest.main()
