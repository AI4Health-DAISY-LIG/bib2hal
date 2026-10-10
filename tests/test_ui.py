"""Offline tests of the local web interface API (real HTTP server on 127.0.0.1, mocked network)."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from bib2hal import credentials as cred, ui
from test_bib2hal import HERE, fake_get_json
from test_domains import FAKE_REF


class TestUI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"BIB2HAL_ME": "", "BIB2HAL_IDHAL": "", "BIB2HAL_STRUCTS": "",
                                           "BIB2HAL_STRUCTS_SINCE": "", "BIB2HAL_DOMAINS": "",
                                           "HAL_LOGIN": "", "HAL_PASSWORD": ""})
        env.start()
        self.addCleanup(env.stop)
        for target in ("bib2hal.crossref.get_json", "bib2hal.hal.get_json"):
            p = mock.patch(target, side_effect=fake_get_json)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("bib2hal.domains.get_json", return_value=FAKE_REF)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("bib2hal.domains.CACHE", self.dir / "cache" / "hal_domains.csv")
        p.start()
        self.addCleanup(p.stop)
        self.sent = []
        self.keychain = {}

        class FakeKeychain:                       # in-memory stand-in for the OS keychain
            label = "Test Keychain"
            get = staticmethod(lambda t: self.keychain.get(t))
            set = staticmethod(lambda t, v: self.keychain.__setitem__(t, v))
            delete = staticmethod(lambda t: self.keychain.pop(t, None))

        for name, val in (("_backend", FakeKeychain()), ("_backend_checked", True), ("_session", {})):
            p = mock.patch.object(cred, name, val)
            p.start()
            self.addCleanup(p.stop)

        def fake_sword(payload, is_zip, login, pwd, filename="meta.xml", test=False):
            self.sent.append((login, test))
            return {"ok": True, "status": 202 if test else 201, "hal_id": "" if test else f"hal-0400000{len(self.sent)}",
                    "message": "", "raw": ""}

        def fake_check(login, pwd):
            ok = (login, pwd) == ("me", "secret")
            return {"ok": ok, "status": 200 if ok else 401, "message": "" if ok else "HAL refused this login or password."}

        self.httpd, self.app, self.url = ui.serve(0, workdir=self.dir,
                                                 app=ui.App(self.dir, deposit_fn=fake_sword, check_fn=fake_check))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, path, data=None, raw=None, token=True, headers=None):
        h = {"X-Bib2hal-Token": self.app.token} if token else {}
        h.update(headers or {})
        body = None
        if data is not None:
            body, h["Content-Type"] = json.dumps(data).encode(), "application/json"
        if raw is not None:
            body = raw
        req = urllib.request.Request(self.url.rstrip("/") + path, data=body, headers=h,
                                     method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait_job(self):
        for _ in range(100):
            _, j = self.call("/api/job")
            if not j["running"]:
                return j
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_security(self):
        with urllib.request.urlopen(self.url) as r:
            html = r.read().decode()
        self.assertIn(self.app.token, html)
        self.assertEqual(self.call("/api/state", token=False)[0], 403)
        self.assertEqual(self.call("/api/state", headers={"X-Bib2hal-Token": "wrong"})[0], 403)
        req = urllib.request.Request(self.url + "api/state", headers={"Host": "evil.example", "X-Bib2hal-Token": self.app.token})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)

    def test_full_pipeline(self):
        # 1. settings: HAL credentials can no longer be set through the settings form
        code, _ = self.call("/api/config", {"values": {"BIB2HAL_ME": "Muller, Sandrine", "BIB2HAL_IDHAL": "sandrine-muller",
                                                       "BIB2HAL_STRUCTS": "1234", "BIB2HAL_STRUCTS_SINCE": "2021",
                                                       "BIB2HAL_DOMAINS": "info.info-ai;sdv.neu",
                                                       "HAL_LOGIN": "me", "HAL_PASSWORD": "secret"}})
        self.assertEqual(code, 200)
        env = (self.dir / ".env").read_text(encoding="utf-8")
        self.assertIn("BIB2HAL_IDHAL=sandrine-muller", env)
        self.assertNotIn("secret", env)
        self.assertNotIn("HAL_LOGIN", env)
        _, st = self.call("/api/state")
        self.assertNotIn("HAL_PASSWORD", st["config"])
        self.assertFalse(st["auth"]["signed_in"])

        # 1b. sign in: wrong password refused by HAL, right one accepted and remembered in the keychain
        code, d = self.call("/api/auth/login", {"login": "me", "password": "nope"})
        self.assertEqual(code, 401)
        self.assertIn("refused", d["error"])
        self.assertEqual(self.call("/api/deposit", {"test": True})[0], 400)            # not signed in
        code, d = self.call("/api/auth/login", {"login": "me", "password": "secret",
                                                "remember": True})
        self.assertEqual(code, 200)
        self.assertEqual((d["auth"]["signed_in"], d["auth"]["login"], d["auth"]["source"]), (True, "me", "keychain"))
        self.assertEqual(list(self.keychain), ["hal"])
        self.assertNotIn("secret", (self.dir / ".env").read_text(encoding="utf-8"))
        cred._session.clear()                                # restart: still signed in from the keychain
        self.assertEqual(cred.get(), ("me", "secret"))
        with mock.patch.dict(os.environ, {"HAL_LOGIN": "x", "HAL_PASSWORD": "plain"}):   # .env is never read for login
            cred._session.clear()
            self.keychain.clear()
            self.assertIsNone(cred.get())
            _, st = self.call("/api/state")
            self.assertTrue(st["auth"]["env_password"])                                # ...but we offer to delete it
        self.call("/api/auth/login", {"login": "me", "password": "secret", "remember": True})

        # 2. domains search + labels
        _, d = self.call("/api/domains?q=neuro")
        self.assertEqual([x["code"] for x in d["results"]], ["sdv.neu"])

        # 3. import
        code, d = self.call("/api/import?name=scholar.bib", raw=(HERE / "sample.bib").read_bytes())
        self.assertEqual(code, 200)
        j = self.wait_job()
        self.assertEqual(j["error"], "")
        self.assertTrue((self.dir / "scholar.bib").exists() and (self.dir / "review.csv").exists())
        _, d = self.call("/api/rows")
        rows = {r["key"]: r for r in d["rows"]}
        conf = rows["muller2023kg"]
        self.assertEqual(conf["action"], "deposit")
        self.assertEqual(conf["problems"], [])
        self.assertIn("sdv.neu", d["labels"])

        # 3b. why 'check' rows need checking
        self.assertEqual([x["code"] for x in rows["homonym2010"]["reasons"]], ["not_me", "no_crossref"])
        self.assertIn("me_position", rows["homonym2010"]["reasons"][0]["fields"])
        self.assertEqual([x["code"] for x in rows["muller2024preprint"]["reasons"]], ["no_crossref"])

        # 3c. bulk change: several rows at once
        ids = [rows["homonym2010"]["id"], rows["muller2020deep_dup"]["id"]]
        code, _ = self.call("/api/bulk", {"ids": ids, "fields": {"action": "skip", "validated": "yes"},
                                          "add_domain": "sdv.neu"})
        self.assertEqual(code, 200)
        self.assertEqual(self.call("/api/bulk", {"ids": ids, "fields": {"action": "nonsense"}})[0], 400)
        _, d = self.call("/api/rows")
        after = {r["key"]: r for r in d["rows"]}
        for k in ("homonym2010", "muller2020deep_dup"):
            self.assertEqual((after[k]["action"], after[k]["validated"]), ("skip", "yes"))
            self.assertIn("sdv.neu", after[k]["domain"].split(";"))
        self.assertEqual(after["muller2023kg"]["action"], "deposit")       # untouched

        # 4. edit a row: make the preprint depositable
        pre = rows["muller2024preprint"]
        code, d = self.call(f"/api/rows/{pre['id']}", {"action": "deposit", "validated": "yes", "my_structs": "1234"})
        self.assertEqual(code, 200)
        self.assertEqual(d["row"]["problems"], [])
        _, d = self.call(f"/api/tei/{conf['id']}")
        self.assertIn("halTypology", d["xml"])

        # 5. test with HAL (X-test): nothing created, does not count as deposited
        self.call("/api/deposit", {"keys": ["muller2023kg"], "test": True})
        self.wait_job()
        self.assertEqual(self.sent, [("me", True)])
        _, d = self.call("/api/ready")
        tested = {r["key"]: r for r in d["ready"]}["muller2023kg"]
        self.assertEqual((tested["done"], tested["test"]["ok"]), (False, True))
        self.sent.clear()

        # 5b. deposit: refused without confirmation; confirmed twice -> sent once
        self.assertEqual(self.call("/api/deposit", {"keys": ["muller2023kg"]})[0], 400)
        _, d = self.call("/api/ready")
        self.assertEqual(sorted(r["key"] for r in d["ready"]), ["muller2023kg", "muller2024preprint"])
        self.call("/api/deposit", {"keys": ["muller2023kg"], "confirm": "yes"})
        self.wait_job()
        self.call("/api/deposit", {"keys": ["muller2023kg"], "confirm": "yes"})
        self.wait_job()
        self.assertEqual(self.sent, [("me", False)])
        _, d = self.call("/api/log")
        self.assertEqual(d["log"][0]["hal_id"], "hal-04000001")

        # 5c. sign out removes it from the keychain too
        _, d = self.call("/api/auth/logout", {})
        self.assertFalse(d["auth"]["signed_in"])
        self.assertEqual(self.keychain, {})

        # 6. re-import keeps a backup of the previous review
        self.call("/api/import?name=scholar.bib", raw=(HERE / "sample.bib").read_bytes())
        self.wait_job()
        self.assertEqual(len(list(self.dir.glob("review-backup-*.csv"))), 1)


if __name__ == "__main__":
    unittest.main()
