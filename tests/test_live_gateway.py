import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from isup_gateway.hevc import AnnexBParser
from isup_gateway.live_source import Subscription, SharedSource
from isup_gateway.settings import SourceSettings, load_settings, public_settings, Target
from isup_gateway.isapi import isapi_response


def nal(kind, body=b"X"):
    return b"\0\0\0\1" + bytes([kind << 1, 1]) + body


class LiveGatewayTests(unittest.TestCase):
    def test_config_summary_and_repr_hide_credentials(self):
        source = SourceSettings("http://localhost:8088", "gateway", "private-source-secret")
        target = Target(
            "a",
            "192.0.2.10",
            7661,
            "TEST001",
            "Camera",
            "private-isup-secret",
            api_base="http://192.0.2.10:8889",
            api_username="admin",
            api_password="private-api-secret",
        )
        rendered = repr(source) + repr(target) + json.dumps(public_settings(source, [target]))
        for secret in (source.password, target.isup_key, target.api_password):
            self.assertNotIn(secret, rendered)

    def test_invalid_config_types_rejected_before_network_io(self):
        original = json.loads(
            (Path(__file__).resolve().parents[1] / "examples" / "gateway.single.json").read_text()
        )
        cases = [
            ("port", True),
            ("port", "7661"),
            ("enabled", "true"),
            ("host", "http://192.0.2.10"),
            ("device_id", "含中文"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            for key, value in cases:
                raw = json.loads(json.dumps(original))
                raw["targets"][0][key] = value
                path.write_text(json.dumps(raw))
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    load_settings(path)

    def test_incremental_realistic_hevc_boundaries(self):
        # Split every possible pipe boundary, including start codes and NAL headers.
        stream = (
            nal(35) + nal(32) + nal(33) + nal(34) + nal(20) + nal(35) + nal(1) + nal(35) + nal(1)
        )
        expected = None
        for size in range(1, 20):
            parser, result = AnnexBParser(), []
            for i in range(0, len(stream), size):
                result.extend(parser.feed(stream[i : i + size]))
            if expected is None:
                expected = result
            self.assertEqual(result, expected)
        self.assertEqual(len(expected), 2)
        self.assertTrue(expected[0][1])
        self.assertFalse(expected[1][1])
        self.assertEqual([n[0] >> 1 for n in expected[0][0]], [32, 33, 34, 20])

    def test_slow_platform_drops_until_next_idr(self):
        sub = Subscription(capacity=2)
        sub.feed((1, []), False)
        self.assertTrue(sub.queue.empty())
        sub.feed((2, []), True)
        sub.feed((3, []), False)
        sub.feed((4, []), False)
        self.assertTrue(sub.queue.empty())
        sub.feed((5, []), True)
        self.assertEqual(sub.queue.get_nowait()[0], 5)
        self.assertEqual(sub.dropped, 2)

    def test_config_rejects_duplicate_registration_and_long_id(self):
        raw = json.loads(
            (Path(__file__).resolve().parents[1] / "examples" / "gateway.single.json").read_text()
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps(raw))
            source, targets = load_settings(path)
            self.assertEqual(targets[0].device_name, "Gateway camera A")
            raw["targets"].append(dict(raw["targets"][0], label="second"))
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_settings(path)
            raw["targets"].pop()
            raw["targets"][0]["device_id"] = "1234567890123"
            path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                load_settings(path)

    def test_device_name_xml_escaping(self):
        body = isapi_response(b"GET /ISAPI/System/Video/inputs/channels", "A&B <live>")
        import xml.etree.ElementTree as ET

        root = ET.fromstring(body)
        self.assertEqual(next(n for n in root.iter() if n.tag.endswith("}name")).text, "A&B <live>")

    def test_shared_source_stays_running_until_last_consumer_stops(self):
        class Client:
            def request(self, *args):
                return {"ok": True}

        class Source(SharedSource):
            def _encode(self, stop):
                with self.lock:
                    self.starts += 1
                    self.active = True
                stop.wait()
                self.active = False

        source = Source(SourceSettings("http://localhost", "gateway", "test"))
        source.client = Client()

        def wait_for(predicate):
            end = time.monotonic() + 2
            while not predicate() and time.monotonic() < end:
                time.sleep(0.005)
            self.assertTrue(predicate())

        try:
            first, _ = source.subscribe()
            wait_for(lambda: source.active)
            second, _ = source.subscribe()
            self.assertEqual(source.starts, 1)
            source.unsubscribe(first)
            self.assertTrue(source.active)
            self.assertFalse(source.stop_event.is_set())
            source.unsubscribe(second)
            wait_for(lambda: source.thread is None)
            self.assertFalse(source.active)
        finally:
            source.close()

    def test_subscription_during_source_cleanup_restarts_worker(self):
        releasing, release_allowed = threading.Event(), threading.Event()

        class Client:
            def request(self, *args):
                releasing.set()
                release_allowed.wait(timeout=2)
                return {"ok": True}

        class Source(SharedSource):
            def _encode(self, stop):
                with self.lock:
                    self.starts += 1
                    self.active = True
                stop.wait()
                self.active = False

        source = Source(SourceSettings("http://localhost", "gateway", "test"))
        source.client = Client()
        try:
            first, _ = source.subscribe()
            source.unsubscribe(first)
            self.assertTrue(releasing.wait(timeout=2))
            second, _ = source.subscribe()
            release_allowed.set()
            end = time.monotonic() + 2
            while source.starts < 2 and time.monotonic() < end:
                time.sleep(0.005)
            self.assertEqual(source.starts, 2)
            self.assertTrue(source.active)
            source.unsubscribe(second)
        finally:
            release_allowed.set()
            source.close()


if __name__ == "__main__":
    unittest.main()
