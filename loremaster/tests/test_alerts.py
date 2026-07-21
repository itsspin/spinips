import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))
SPEC = importlib.util.spec_from_file_location(
    "loremaster_alerts_test_app", LOREMASTER_DIR / "loremaster.py")
LOREMASTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOREMASTER
SPEC.loader.exec_module(LOREMASTER)

from wiki_overlay import WikiItem  # noqa: E402


def base_cfg(**overrides):
    cfg = {"alerts_enabled": True, "big_hit_threshold": 800}
    cfg.update(overrides)
    return cfg


class AlertTriggerGatingTests(unittest.TestCase):
    def test_all_builtin_triggers_fire_by_default(self):
        cfg = base_cfg()
        self.assertTrue(LOREMASTER.check_alerts(
            "tell_in", {"sender": "Foo", "msg": "hi"}, "", "Soandso", cfg))
        self.assertTrue(LOREMASTER.check_alerts("summoned", {}, "", "Soandso", cfg))
        self.assertTrue(LOREMASTER.check_alerts(
            "death_you", {"killer": "a dragon"}, "", "Soandso", cfg))
        self.assertTrue(LOREMASTER.check_alerts(
            "melee_in", {"dmg": "900"}, "", "Soandso", cfg))
        called = LOREMASTER.check_alerts(
            "", {}, "Bob tells the group, 'Heal Soandso now'", "Soandso", cfg)
        self.assertTrue(called)
        self.assertIn("BOB CALLED YOU", called[0][1])

    def test_each_trigger_has_its_own_off_switch(self):
        cases = [
            ("alert_tells", "tell_in", {"sender": "Foo", "msg": "hi"}, ""),
            ("alert_summon", "summoned", {}, ""),
            ("alert_death", "death_you", {"killer": "a dragon"}, ""),
            ("alert_big_hit", "melee_in", {"dmg": "9000"}, ""),
            ("alert_name_called", "", {},
             "Bob tells the raid, 'Soandso to the front'"),
        ]
        for key, kind, groups, raw in cases:
            with self.subTest(key=key):
                on = LOREMASTER.check_alerts(
                    kind, groups, raw, "Soandso", base_cfg())
                off = LOREMASTER.check_alerts(
                    kind, groups, raw, "Soandso", base_cfg(**{key: False}))
                self.assertTrue(on)
                self.assertEqual(off, [])

    def test_master_switch_still_silences_everything(self):
        cfg = base_cfg(alerts_enabled=False)
        self.assertEqual(LOREMASTER.check_alerts(
            "summoned", {}, "", "Soandso", cfg), [])

    def test_disabling_one_trigger_leaves_the_others_alone(self):
        cfg = base_cfg(alert_big_hit=False)
        self.assertEqual(LOREMASTER.check_alerts(
            "melee_in", {"dmg": "9000"}, "", "Soandso", cfg), [])
        self.assertTrue(LOREMASTER.check_alerts(
            "summoned", {}, "", "Soandso", cfg))


class FightToastGatingTests(unittest.TestCase):
    def test_master_switch_gates_fight_toasts(self):
        self.assertTrue(LOREMASTER.fight_toasts_active({}))
        self.assertTrue(LOREMASTER.fight_toasts_active(
            {"alerts_enabled": True, "fight_toasts": True}))
        self.assertFalse(LOREMASTER.fight_toasts_active(
            {"alerts_enabled": False, "fight_toasts": True}))
        self.assertFalse(LOREMASTER.fight_toasts_active(
            {"alerts_enabled": True, "fight_toasts": False}))


class AlertConfigDefaultTests(unittest.TestCase):
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

    def test_per_trigger_defaults_are_all_on(self):
        cfg = self.load_payload({})
        for key in ("alert_tells", "alert_summon", "alert_death",
                    "alert_big_hit", "alert_name_called"):
            self.assertIs(cfg[key], True, key)
        self.assertIs(cfg["alerts_enabled"], True)
        self.assertIs(cfg["fight_toasts"], True)
        self.assertEqual(cfg["big_hit_threshold"], 800)
        self.assertEqual(cfg["alert_seconds"], 4)
        self.assertIsNone(cfg["alert_position"])

    def test_saved_trigger_choices_survive_reload(self):
        cfg = self.load_payload({"alert_tells": False, "alert_seconds": 9})
        self.assertIs(cfg["alert_tells"], False)
        self.assertEqual(cfg["alert_seconds"], 9)

    def test_invalid_custom_alert_patterns_warn_once_on_load(self):
        payload = {"custom_alerts": [
            {"pattern": "(broken", "text": "x"},
            {"pattern": "fine.*", "text": "y"},
        ]}
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            self.load_payload(payload)
        output = captured.getvalue()
        self.assertIn("1 invalid custom alert pattern", output)
        self.assertIn("(broken", output)

    def test_valid_custom_alert_patterns_load_silently(self):
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            self.load_payload({"custom_alerts": [{"pattern": "ok", "text": "y"}]})
        self.assertEqual(captured.getvalue(), "")


class CustomAlertValidationTests(unittest.TestCase):
    def test_only_broken_regexes_are_reported(self):
        rules = [
            {"pattern": "valid.*"},
            {"pattern": "(unclosed"},
            {"pattern": "[bad"},
            {"text": "no pattern key"},
            "not a dict",
        ]
        self.assertEqual(
            LOREMASTER.invalid_custom_alert_patterns(rules),
            ["(unclosed", "[bad"])
        self.assertEqual(LOREMASTER.invalid_custom_alert_patterns(None), [])
        self.assertEqual(LOREMASTER.invalid_custom_alert_patterns([]), [])


class AlertPositionClampTests(unittest.TestCase):
    BOUNDS = (-1920, 0, 3840, 1080)  # left monitor + primary

    def test_offscreen_position_is_pulled_back_onto_the_desktop(self):
        x, y = LOREMASTER.clamp_alert_position(
            (99999, 99999), 300, 50, self.BOUNDS, 0, 64)
        self.assertEqual((x, y), (-1920 + 3840 - 300, 1080 - 50))
        x, y = LOREMASTER.clamp_alert_position(
            (-99999, -99999), 300, 50, self.BOUNDS, 0, 64)
        self.assertEqual((x, y), (-1920, 0))

    def test_valid_position_including_left_monitor_is_untouched(self):
        self.assertEqual(LOREMASTER.clamp_alert_position(
            (-1500, 200), 300, 50, self.BOUNDS, 0, 64), (-1500, 200))

    def test_garbage_position_falls_back_to_default(self):
        for pos in (None, "junk", [], ["x", "y"], {"a": 1}):
            with self.subTest(pos=pos):
                self.assertEqual(LOREMASTER.clamp_alert_position(
                    pos, 300, 50, self.BOUNDS, 120, 64), (120, 64))


class CaptureAnchorRescaleTests(unittest.TestCase):
    def test_150_percent_dpi_scales_physical_cursor_into_logical_pixels(self):
        physical = (0, 0, 2880, 1620)   # 1920x1080 desktop at 150% DPI
        logical = (0, 0, 1920, 1080)
        self.assertEqual(LOREMASTER.rescale_capture_anchor(
            1440, 810, physical, logical), (960, 540))
        self.assertEqual(LOREMASTER.rescale_capture_anchor(
            2880, 1620, physical, logical), (1920, 1080))

    def test_multi_monitor_negative_origin_is_preserved(self):
        physical = (-3840, 0, 7680, 2160)
        logical = (-1920, 0, 3840, 1080)
        self.assertEqual(LOREMASTER.rescale_capture_anchor(
            -3840, 0, physical, logical), (-1920, 0))
        self.assertEqual(LOREMASTER.rescale_capture_anchor(
            0, 1080, physical, logical), (0, 540))

    def test_identity_when_dpi_matches(self):
        bounds = (0, 0, 1920, 1080)
        self.assertEqual(LOREMASTER.rescale_capture_anchor(
            123, 456, bounds, bounds), (123, 456))

    def test_zero_sized_bounds_fall_back_to_raw_cursor(self):
        good = (0, 0, 1920, 1080)
        for bad in ((0, 0, 0, 1080), (0, 0, 1920, 0), (0, 0, -5, -5)):
            with self.subTest(bad=bad):
                self.assertEqual(LOREMASTER.rescale_capture_anchor(
                    700, 800, bad, good), (700, 800))
                self.assertEqual(LOREMASTER.rescale_capture_anchor(
                    700, 800, good, bad), (700, 800))


class WikiStatusLabelTests(unittest.TestCase):
    def make_item(self, cached=False, stale=False, age=0.0):
        item = WikiItem(title="Cloak of Flames", url="https://example.test",
                        fetched_at=time.time() - age)
        item.cached = cached
        item.stale = stale
        return item

    def test_fresh_network_result_reads_live(self):
        self.assertEqual(
            LOREMASTER.wiki_status_label(self.make_item()), "LIVE")

    def test_valid_cache_shows_cached_with_age(self):
        label = LOREMASTER.wiki_status_label(
            self.make_item(cached=True, age=7200))
        self.assertEqual(label, "CACHED 2H AGO")

    def test_stale_cache_stays_clearly_marked(self):
        label = LOREMASTER.wiki_status_label(
            self.make_item(cached=True, stale=True, age=3 * 86400))
        self.assertEqual(label, "STALE CACHE 3D AGO")


if __name__ == "__main__":
    unittest.main()
