import unittest
from datetime import datetime, timedelta
from progression import ProgressionTracker


class ProgressionTests(unittest.TestCase):
    def setUp(self):
        self.t = datetime(2026, 8, 25, 12)
        self.p = ProgressionTracker()

    def after(self, seconds):
        return self.t + timedelta(seconds=seconds)

    def test_measured_rate_and_eta(self):
        self.p.observe(self.t, "zone", {})
        self.p.checkpoint(self.t, 40, 50)
        self.p.observe(self.after(30), "xp", {"pct": "2"})
        self.assertIsNone(self.p.snapshot(self.after(59))["percentPerHour"])
        view = self.p.snapshot(self.after(120))
        self.assertEqual(view["percentPerHour"], 60)
        self.assertEqual(view["currentPercent"], 52)
        self.assertEqual(view["secondsToLevel"], 2880)

    def test_unknown_gains_never_invent_rates(self):
        self.p.observe(self.t, "xp", {"pct": "2"})
        self.p.observe(self.after(1), "xp", {})
        view = self.p.snapshot(self.after(120))
        self.assertIsNone(view["percentPerHour"])
        self.assertIsNone(view["currentPercent"])

    def test_checkpoints_and_expiry(self):
        self.p.checkpoint(self.t, 40, 50)
        self.p.checkpoint(self.after(120), 40, 52)
        self.assertEqual(self.p.snapshot(self.after(120))["percentPerHour"], 60)
        self.assertEqual(self.p.snapshot(self.after(120))["rateSource"], "checkpoints")
        self.assertIsNone(self.p.snapshot(self.after(1921))["percentPerHour"])

    def test_loss_and_level_change_reset_rates(self):
        for kind, fields in (("death_you", {}), ("level", {"level": "41"})):
            with self.subTest(kind=kind):
                self.setUp()
                self.p.observe(self.t, "xp", {"pct": "5"})
                self.p.checkpoint(self.t, 40, 50)
                self.p.checkpoint(self.after(120), 40, 52)
                self.p.observe(self.after(180), kind, fields)
                self.assertIsNone(self.p.snapshot(self.after(300))["percentPerHour"])
                self.assertIsNone(self.p.snapshot(self.after(300))["secondsToLevel"])
        self.p.checkpoint(self.after(400), 42, 20)
        self.assertIsNone(self.p.snapshot(self.after(500))["percentPerHour"])

    def test_checkpoints_reject_bad_values(self):
        for level, percent in ((True, 10), (0, 0), (126, 0), (1, 100), (1, -1), (1, float("nan")), (1, float("inf"))):
            with self.assertRaises(ValueError):
                self.p.checkpoint(self.t, level, percent)

    def test_history_is_bounded_and_zero_is_valid(self):
        for n in range(1000):
            self.p.observe(self.after(n), "xp", {"pct": 0})
        self.assertEqual(len(self.p.history), 60)
        self.assertEqual(self.p.snapshot(self.after(1000))["percentPerHour"], 0)
        self.assertIsNone(self.p.snapshot(self.after(1000))["secondsToLevel"])

    def test_aa_and_idle_decay(self):
        self.p.observe(self.t, "aa", {})
        self.assertEqual(self.p.snapshot(self.after(3600))["aaPerHour"], 1)
        self.assertEqual(self.p.snapshot(self.after(7200))["aaPerHour"], .5)

    def test_decreasing_checkpoint_starts_new_interval(self):
        self.p.checkpoint(self.t, 40, 50)
        self.p.checkpoint(self.after(120), 40, 40)
        self.assertIsNone(self.p.snapshot(self.after(120))["percentPerHour"])
