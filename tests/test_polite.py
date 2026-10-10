"""Offline tests: bib2hal stays polite and stops when a service shows a bot-protection page."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bib2hal import pipeline as pl, util
from bib2hal.util import BlockedError, looks_blocked

HERE = Path(__file__).parent
ANUBIS = "<!doctype html><html><head><title>Making sure you're not a bot!</title></head><body>Anubis</body></html>"


class TestPolite(unittest.TestCase):
    def test_detection(self):
        self.assertTrue(looks_blocked(200, ANUBIS, "text/html"))
        self.assertTrue(looks_blocked(429, "", ""))
        self.assertFalse(looks_blocked(200, '{"response": {}}', "application/json"))
        self.assertFalse(looks_blocked(200, "<?xml version='1.0'?><entry/>", "application/atom+xml"))

    def test_throttle(self):
        host = "https://api.archives-ouvertes.fr/search/"
        with mock.patch.dict(util.MIN_INTERVAL, {"api.archives-ouvertes.fr": 0.2}):
            util._last_call.pop("api.archives-ouvertes.fr", None)
            t = time.monotonic()
            util._throttle(host)
            util._throttle(host)
            self.assertGreaterEqual(time.monotonic() - t, 0.19)

    def test_review_stops_querying_hal_when_blocked(self):
        calls = []

        def blocked(*a, **k):
            calls.append(1)
            raise BlockedError("protection page")

        with mock.patch("bib2hal.pipeline.find", side_effect=blocked):
            rows = pl.review_bib((HERE / "sample.bib").read_text(encoding="utf-8"), no_crossref=True)
        self.assertEqual(len(calls), 1)                      # asked once, then stopped
        self.assertTrue(all(r["hal_status"] in ("hal-error", "not-checked") for r in rows))
        self.assertTrue(all(r["action"] != "deposit" for r in rows))

    def test_deposit_stops_when_blocked(self):
        rows = [{"key": f"k{i}", "title": f"T{i}", "authors": "Muller, S", "hal_type": "UNDEFINED", "year": "2024",
                 "domain": "info", "language": "en", "me_position": "1", "my_structs": "1", "pdf": ""}
                for i in range(3)]
        sent = []

        def fn(*a, **k):
            sent.append(1)
            raise BlockedError("protection page")

        with tempfile.TemporaryDirectory() as d:
            res = pl.deposit(rows, "me", "pw", Path(d) / "log.csv", deposit_fn=fn)
        self.assertEqual(len(sent), 1)
        self.assertFalse(res[0]["ok"])


if __name__ == "__main__":
    unittest.main()
