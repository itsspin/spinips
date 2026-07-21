import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import URLError


LOREMASTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOREMASTER_DIR))

from wiki_overlay import (  # noqa: E402
    LOOKUP_REQUEST_BUDGET_SECONDS,
    MAX_RESPONSE_BYTES,
    WikiCache,
    WikiClient,
    WikiError,
    WikiItem,
    WikiLookupService,
    WikiNotFoundError,
    WikiOfflineError,
    clipboard_lookup_plan,
    extract_item_query,
    extract_structured_item_query,
    hotkey_lookup_plan,
    parse_hotkey,
    parse_item_payload,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.payload


class WikiParsingTests(unittest.TestCase):
    def fixture(self, name):
        return (FIXTURES / name).read_bytes()

    def test_cloak_of_flames_profile_and_source(self):
        item = parse_item_payload(self.fixture("cloak_of_flames.json"),
                                  "Cloak of Flames +4")
        self.assertEqual(item.title, "Cloak of Flames")
        self.assertIn("Haste: +36%", item.stats)
        self.assertEqual(item.sections["Drops From"][:2], [
            "Nagafen's Lair", "• Lord Nagafen",
        ])
        self.assertTrue(item.url.endswith("/Cloak_of_Flames"))

    def test_nested_item_templates_are_sanitized(self):
        item = parse_item_payload(self.fixture("studded_belt.json"), "Studded Belt")
        crafted = item.sections["Player crafted"]
        self.assertIn("Merchant value: 2gp", item.stats)
        self.assertIn("Focus Effect: Reagent Conservation I", item.stats)
        self.assertIn("• Tailoring (Trivial: 56)", crafted)
        self.assertTrue(any("Medium Quality Cat Skin" in row for row in crafted))
        self.assertFalse(any("SmIcon" in row or "{{" in row for row in crafted))

    def test_non_item_page_is_rejected(self):
        payload = {"parse": {"title": "Dreadlands", "wikitext": {"*": "A zone page."}}}
        with self.assertRaises(WikiNotFoundError):
            parse_item_payload(payload, "Dreadlands")

    def test_clipboard_inputs_and_hotkeys(self):
        self.assertEqual(extract_item_query(
            "https://eqlwiki.com/Cloak_of_Flames")[0], "Cloak of Flames")
        self.assertEqual(extract_item_query("[Cloak of Flames +4]")[0],
                         "Cloak of Flames")
        eq_link = "\x12000000000000000000000000000000000000000000000000000000[Cloak of Flames +4]\x12"
        self.assertEqual(extract_item_query(eq_link)[0], "Cloak of Flames")
        self.assertEqual(extract_structured_item_query(eq_link)[0], "Cloak of Flames")
        self.assertIsNone(extract_structured_item_query("Cloak of Flames")[0])
        self.assertEqual(clipboard_lookup_plan("Cloak of Flames"),
                         ("Cloak of Flames", "clipboard text", False))
        self.assertEqual(clipboard_lookup_plan(eq_link),
                         ("Cloak of Flames", "EQ item link", True))
        self.assertEqual(parse_hotkey("ctrl + shift + e"),
                         (0x4006, ord("E"), "Ctrl+Shift+E"))
        self.assertEqual(parse_hotkey("Ctrl+Shift+F12")[2], "Ctrl+Shift+F12")
        with self.assertRaises(ValueError):
            parse_hotkey("E")

    def test_eq_hotkey_prioritizes_hover_over_structured_clipboard(self):
        eq_link = "\x12000000000000000000000000000000000000000000000000000000[Cloak of Flames]\x12"
        self.assertEqual(
            hotkey_lookup_plan(eq_link, eq_foreground=True,
                               hover_scan_enabled=True)[0],
            "hover",
        )
        action, query, source, automatic = hotkey_lookup_plan(
            eq_link, eq_foreground=False, hover_scan_enabled=True)
        self.assertEqual((action, query, source, automatic),
                         ("clipboard", "Cloak of Flames", "EQ item link", True))


class WikiClientTests(unittest.TestCase):
    def setUp(self):
        self.payload = (FIXTURES / "cloak_of_flames.json").read_bytes()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def await_result(self, service, timeout=1.0):
        deadline = time.monotonic() + timeout
        results = []
        while time.monotonic() < deadline and not results:
            results = service.poll()
            time.sleep(0.005)
        self.assertTrue(results, "wiki worker did not produce a result")
        return results[0]

    def test_http_is_mocked_and_fresh_cache_prevents_second_request(self):
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, timeout, request.headers.get("User-agent")))
            return FakeResponse(self.payload)

        cache = WikiCache(Path(self.temp.name), ttl_seconds=3600)
        client = WikiClient(cache, opener=opener, min_request_interval=0)
        first = client.fetch("Cloak of Flames +4")
        second = client.fetch("Cloak of Flames +4")
        self.assertEqual(first.title, second.title)
        self.assertEqual(len(calls), 1)
        self.assertTrue(second.cached)
        self.assertIn("action=parse", calls[0][0])
        self.assertEqual(calls[0][1], 6.0)
        self.assertIn("Spins-Loremaster", calls[0][2])

    def test_stale_cache_is_an_offline_fallback(self):
        clock = [1000.0]
        cache = WikiCache(Path(self.temp.name), ttl_seconds=10,
                          clock=lambda: clock[0])
        item = parse_item_payload(self.payload, "Cloak of Flames")
        item.fetched_at = 1000.0
        cache.put("Cloak of Flames", item)
        clock[0] = 1020.0

        def offline(_request, timeout):
            raise URLError("offline")

        client = WikiClient(cache, opener=offline, min_request_interval=0)
        result = client.fetch("Cloak of Flames")
        self.assertTrue(result.cached)
        self.assertTrue(result.stale)

    def test_network_can_be_disabled_without_live_http(self):
        cache = WikiCache(Path(self.temp.name), ttl_seconds=3600)
        client = WikiClient(cache, network_enabled=False, min_request_interval=0)
        with self.assertRaises(Exception) as raised:
            client.fetch("Uncached Thing")
        self.assertIn("disabled", str(raised.exception).lower())

    def test_not_found_includes_mocked_fuzzy_suggestions(self):
        responses = [
            json.dumps({"error": {"code": "missingtitle"}}).encode(),
            json.dumps({"query": {"pages": {
                "1": {"title": "Cloak of Flames"},
                "2": {"title": "Cloak of Crystalline Waters"},
            }}}).encode(),
        ]

        def opener(_request, timeout):
            return FakeResponse(responses.pop(0))

        client = WikiClient(WikiCache(Path(self.temp.name), 0), opener=opener,
                            min_request_interval=0)
        with self.assertRaises(WikiNotFoundError) as raised:
            client.fetch("Clok of Flames")
        self.assertEqual(raised.exception.suggestions[0], "Cloak of Flames")

    def test_response_size_cap(self):
        def opener(_request, timeout):
            return FakeResponse(b"x" * (MAX_RESPONSE_BYTES + 1))

        client = WikiClient(WikiCache(Path(self.temp.name), 0), opener=opener,
                            min_request_interval=0)
        with self.assertRaises(WikiError) as raised:
            client.fetch("Oversized Item")
        self.assertIn("large", str(raised.exception))

    def test_worker_submission_never_waits_for_network(self):
        gate = threading.Event()
        expected = parse_item_payload(self.payload, "Cloak of Flames")

        class SlowClient:
            def fetch(self, _query):
                gate.wait(0.5)
                return expected

        service = WikiLookupService(SlowClient())
        self.addCleanup(service.close)
        started = time.monotonic()
        request_id = service.submit("Cloak of Flames")
        self.assertLess(time.monotonic() - started, 0.05)
        gate.set()
        deadline = time.monotonic() + 1.0
        results = []
        while time.monotonic() < deadline and not results:
            results = service.poll()
            time.sleep(0.005)
        self.assertEqual(results[0].request_id, request_id)
        self.assertEqual(results[0].item.title, "Cloak of Flames")

    def test_ranked_candidates_are_validated_until_an_exact_item(self):
        expected = parse_item_payload(self.payload, "Cloak of Flames")

        class CandidateClient:
            def __init__(self):
                self.calls = []

            def fetch(self, query):
                self.calls.append(query)
                if query != "Cloak of Flames":
                    raise WikiNotFoundError(query)
                return expected

        client = CandidateClient()
        service = WikiLookupService(client)
        self.addCleanup(service.close)
        request_id = service.submit_candidates(["Description", "Cloak of Flames"])
        deadline = time.monotonic() + 1.0
        results = []
        while time.monotonic() < deadline and not results:
            results = service.poll()
            time.sleep(0.005)
        self.assertEqual(results[0].request_id, request_id)
        self.assertEqual(results[0].item.title, "Cloak of Flames")
        self.assertEqual(client.calls, ["Description", "Cloak of Flames"])

    def test_real_client_candidates_share_one_total_request_deadline(self):
        class FakeClock:
            def __init__(self):
                self.now = 100.0

            def __call__(self):
                return self.now

            def advance(self, seconds):
                self.now += seconds

        clock = FakeClock()
        timeouts = []
        missing = json.dumps({"error": {"code": "missingtitle"}}).encode()

        def opener(_request, timeout):
            timeouts.append(timeout)
            clock.advance(timeout)
            return FakeResponse(missing)

        client = WikiClient(
            WikiCache(Path(self.temp.name), 0),
            timeout=6.0,
            opener=opener,
            min_request_interval=0,
            monotonic=clock,
        )
        service = WikiLookupService(client)
        self.addCleanup(service.close)
        service.submit_candidates([
            "First Candidate", "Second Candidate",
            "Third Candidate", "Fourth Candidate",
        ])

        result = self.await_result(service)
        self.assertIsInstance(result.error, WikiOfflineError)
        self.assertEqual(len(timeouts), 2)
        self.assertAlmostEqual(sum(timeouts), LOOKUP_REQUEST_BUDGET_SECONDS)
        self.assertLessEqual(max(timeouts), client.timeout)

    def test_newer_lookup_suppresses_active_generation_and_remaining_candidates(self):
        started = threading.Event()
        release = threading.Event()

        class RapidClient:
            def __init__(self):
                self.calls = []

            def fetch(self, query):
                self.calls.append(query)
                if query == "Old Tooltip Heading":
                    started.set()
                    release.wait(1.0)
                    raise WikiNotFoundError(query)
                return WikiItem(title=query, url=f"https://eqlwiki.com/{query}")

        client = RapidClient()
        service = WikiLookupService(client)
        self.addCleanup(service.close)
        old_request = service.submit_candidates([
            "Old Tooltip Heading", "Old Item Candidate",
        ])
        self.assertTrue(started.wait(0.5))
        new_request = service.submit("New Hovered Item")
        release.set()

        result = self.await_result(service)
        self.assertNotEqual(old_request, new_request)
        self.assertEqual(result.request_id, new_request)
        self.assertEqual(result.query, "New Hovered Item")
        self.assertEqual(result.item.title, "New Hovered Item")
        self.assertEqual(client.calls, ["Old Tooltip Heading", "New Hovered Item"])
        self.assertEqual(service.poll(), [])

    def test_offline_lookup_finds_stale_cache_for_later_candidate(self):
        wall_clock = [1000.0]
        cache = WikiCache(
            Path(self.temp.name), ttl_seconds=10, clock=lambda: wall_clock[0])
        item = parse_item_payload(self.payload, "Cloak of Flames")
        item.fetched_at = wall_clock[0]
        cache.put("Cloak of Flames", item)
        wall_clock[0] += 20.0

        client = WikiClient(cache, network_enabled=False, min_request_interval=0)
        service = WikiLookupService(client)
        self.addCleanup(service.close)
        request_id = service.submit_candidates(["Description", "Cloak of Flames"])

        result = self.await_result(service)
        self.assertEqual(result.request_id, request_id)
        self.assertEqual(result.query, "Cloak of Flames")
        self.assertEqual(result.item.title, "Cloak of Flames")
        self.assertTrue(result.item.cached)
        self.assertTrue(result.item.stale)


if __name__ == "__main__":
    unittest.main()
