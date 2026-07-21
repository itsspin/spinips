import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))
SPEC = importlib.util.spec_from_file_location(
    "loremaster_config_test_app", LOREMASTER_DIR / "loremaster.py")
LOREMASTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOREMASTER
SPEC.loader.exec_module(LOREMASTER)


class ConfigRecoveryTests(unittest.TestCase):
    def load_payload(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "loremaster_config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            original = LOREMASTER.CONFIG_PATH
            LOREMASTER.CONFIG_PATH = path
            try:
                return LOREMASTER.load_config()
            finally:
                LOREMASTER.CONFIG_PATH = original

    def test_valid_non_object_json_falls_back_to_defaults(self):
        for payload in (None, [], "unexpected", 42):
            with self.subTest(payload=payload):
                config = self.load_payload(payload)
                self.assertEqual(config["wiki_hotkey"], "Ctrl+Shift+E")
                self.assertEqual(config["opacity"], 1.0)

    def test_legacy_visual_and_hotkey_defaults_migrate(self):
        config = self.load_payload({"opacity": 0.94, "wiki_hotkey": "Alt+E"})
        self.assertEqual(config["opacity"], 1.0)
        self.assertEqual(config["wiki_hotkey"], "Ctrl+Shift+E")
        self.assertEqual(config["ui_rendering_version"], 2)

    def test_explicit_custom_hotkey_and_opacity_are_preserved(self):
        config = self.load_payload({
            "opacity": 0.90,
            "wiki_hotkey": "Alt+E",
            "wiki_hotkey_customized": True,
        })
        self.assertEqual(config["opacity"], 0.90)
        self.assertEqual(config["wiki_hotkey"], "Alt+E")


if __name__ == "__main__":
    unittest.main()
