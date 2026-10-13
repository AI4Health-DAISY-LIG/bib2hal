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
        with urllib.request.urlopen(self.url + "static/fonts/dm-sans-latin-var.woff2") as r:   # bundled assets
            self.assertEqual(r.headers["Content-Type"], "font/woff2")
        for bad in ("static/../ui.py", "static/index.html", "static/%2e%2e/ui.py"):
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(self.url + bad)

    def test_full_pipeline(self):
        # 1. settings: HAL credentials can no longer be set through the settings form
        code, _ = self.call("/api/config", {"values": {"BIB2HAL_ME": "Dupont, Ada", "BIB2HAL_IDHAL": "ada-dupont",
                                                       "BIB2HAL_STRUCTS": "1234", "BIB2HAL_STRUCTS_SINCE": "2021",
                                                       "BIB2HAL_DOMAINS": "info.info-ai;sdv.neu",
                                                       "HAL_LOGIN": "me", "HAL_PASSWORD": "secret"}})
        self.assertEqual(code, 200)
        env = (self.dir / ".env").read_text(encoding="utf-8")
        self.assertIn("BIB2HAL_IDHAL=ada-dupont", env)
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
        conf = rows["dupont2023rl"]
        self.assertEqual(conf["action"], "deposit")
        self.assertEqual(conf["problems"], ["my_structs"])                    # affiliation is picked by the user
        _, d2 = self.call(f"/api/rows/{conf['id']}", {"my_structs": "1234"})
        self.assertEqual(d2["row"]["problems"], [])
        self.assertIn("sdv.neu", d["labels"])

        # 3b. why 'check' rows need checking
        self.assertEqual([x["code"] for x in rows["homonym2010"]["reasons"]], ["not_me", "no_crossref"])
        self.assertIn("me_position", rows["homonym2010"]["reasons"][0]["fields"])
        self.assertEqual([x["code"] for x in rows["dupont2024preprint"]["reasons"]], ["no_crossref"])

        # 3c. bulk change: several rows at once
        ids = [rows["homonym2010"]["id"], rows["dupont2020deep_dup"]["id"]]
        code, _ = self.call("/api/bulk", {"ids": ids, "fields": {"action": "skip", "validated": "yes"},
                                          "add_domain": "sdv.neu"})
        self.assertEqual(code, 200)
        self.assertEqual(self.call("/api/bulk", {"ids": ids, "fields": {"action": "nonsense"}})[0], 400)
        _, d = self.call("/api/rows")
        after = {r["key"]: r for r in d["rows"]}
        for k in ("homonym2010", "dupont2020deep_dup"):
            self.assertEqual((after[k]["action"], after[k]["validated"]), ("skip", "yes"))
            self.assertIn("sdv.neu", after[k]["domain"].split(";"))
        self.assertEqual(after["dupont2023rl"]["action"], "deposit")       # untouched

        # 4. edit a row: make the preprint depositable
        pre = rows["dupont2024preprint"]
        code, d = self.call(f"/api/rows/{pre['id']}", {"action": "deposit", "validated": "yes", "my_structs": "1234"})
        self.assertEqual(code, 200)
        self.assertEqual(d["row"]["problems"], [])
        _, d = self.call(f"/api/tei/{conf['id']}")
        self.assertIn("halTypology", d["xml"])

        # 5. test with HAL (X-test): nothing created, does not count as deposited
        self.call("/api/deposit", {"keys": ["dupont2023rl"], "test": True})
        self.wait_job()
        self.assertEqual(self.sent, [("me", True)])
        _, d = self.call("/api/ready")
        tested = {r["key"]: r for r in d["ready"]}["dupont2023rl"]
        self.assertEqual((tested["done"], tested["test"]["ok"]), (False, True))
        self.sent.clear()

        # 5b. deposit: refused without confirmation; confirmed twice -> sent once
        self.assertEqual(self.call("/api/deposit", {"keys": ["dupont2023rl"]})[0], 400)
        _, d = self.call("/api/ready")
        self.assertEqual(sorted(r["key"] for r in d["ready"]), ["dupont2023rl", "dupont2024preprint"])
        self.call("/api/deposit", {"keys": ["dupont2023rl"], "confirm": "yes"})
        self.wait_job()
        self.call("/api/deposit", {"keys": ["dupont2023rl"], "confirm": "yes"})
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

    def _import(self, **q):
        qs = "&".join(f"{k}={v}" for k, v in q.items())
        raw = b"" if "reuse" in q else (HERE / "sample.bib").read_bytes()
        code, d = self.call("/api/import?" + qs, raw=raw)
        self.assertEqual(code, 200, d)
        j = self.wait_job()
        self.assertEqual(j["error"], "")
        return {r["key"]: r for r in self.call("/api/rows")[1]["rows"]}

    def test_reimport_keeps_review_and_skips_known(self):
        rows = self._import(name="scholar.bib")
        conf = rows["dupont2023rl"]
        self.call(f"/api/rows/{conf['id']}", {"title": "My corrected title", "my_structs": "1234", "validated": "yes"})
        # the user adds a paper to the .bib, then re-analyses the file already in the folder
        bib = (self.dir / "scholar.bib").read_text(encoding="utf-8") + (
            "\n@article{new2025,\n title={A brand new paper},\n author={Dupont, Ada},\n journal={J},\n year={2025}\n}\n")
        (self.dir / "scholar.bib").write_text(bib, encoding="utf-8")
        import bib2hal.hal as halmod
        with mock.patch("bib2hal.pipeline.find", wraps=halmod.find) as f:
            _, st = self.call("/api/state")
            self.assertEqual(st["bib_files"][0]["name"], "scholar.bib")
            rows2 = self._import(reuse="scholar.bib")
            self.assertEqual(f.call_count, 1)                       # only the new reference was looked up
        self.assertEqual(len(rows2), 7)
        self.assertEqual(rows2["dupont2023rl"]["title"], "My corrected title")   # edits kept
        self.assertEqual(rows2["dupont2023rl"]["validated"], "yes")
        self.assertIn("new2025", rows2)
        rows3 = self._import(reuse="scholar.bib", mode="replace")    # replace mode starts over
        self.assertNotEqual(rows3["dupont2023rl"]["title"], "My corrected title")

    def test_export_bib(self):
        self._import(name="scholar.bib")
        req = urllib.request.Request(self.url + "api/export", headers={"X-Bib2hal-Token": self.app.token})
        with urllib.request.urlopen(req) as r:
            text = r.read().decode("utf-8")
            self.assertIn("attachment", r.headers["Content-Disposition"])
        from bib2hal.bibtex import parse_bibtex
        keys = [e["ID"] for e in parse_bibtex(text)]
        self.assertIn("dupont2023rl", keys)
        self.assertNotIn("dupont2020deep_dup", keys)               # duplicates left out
        self.assertTrue((self.dir / "bib2hal-export.bib").exists())

    def test_pdf_upload(self):
        rows = self._import(name="scholar.bib")
        i = rows["dupont2023rl"]["id"]
        self.assertEqual(self.call(f"/api/pdf/{i}?name=x.pdf", raw=b"not a pdf")[0], 400)
        code, d = self.call(f"/api/pdf/{i}?name=paper.pdf", raw=b"%PDF-1.4 fake")
        self.assertEqual(code, 200)
        self.assertTrue(Path(d["row"]["pdf"]).is_file())
        self.assertEqual(Path(d["row"]["pdf"]).parent.name, "pdfs")
        _, d = self.call(f"/api/pdf/{i}?remove=1", {})
        self.assertEqual(d["row"]["pdf"], "")

    def test_struct_names(self):
        os.environ["BIB2HAL_STRUCT_NAMES"] = "1234:LAB — Laboratoire d'Exemple"
        with mock.patch("bib2hal.ui.structure_names", return_value={"999": "UDE — Université d'Exemple"}) as m:
            _, d = self.call("/api/structnames?ids=1234;999")
            m.assert_called_once_with(["999"])                      # saved names are not asked again
        self.assertEqual(d["names"], {"1234": "LAB — Laboratoire d'Exemple",
                                      "999": "UDE — Université d'Exemple"})

    def test_failed_submission_is_explained(self):
        rows = self._import(name="scholar.bib")
        conf = rows["dupont2023rl"]
        self.assertEqual(self.call(f"/api/rows/{conf['id']}", {"my_structs": "1234", "me_position": "1"})[1]["row"]["problems"], [])
        self.call("/api/auth/login", {"login": "me", "password": "secret"})
        err = ("ERROR The supplied XML description does not validate the AOfr schema... Element "
               "'{http://www.tei-c.org/ns/1.0}surname': This element is not expected. Expected is "
               "( {http://www.tei-c.org/ns/1.0}forename ).")
        self.app.deposit_fn = lambda *a, **k: {"ok": False, "status": 406, "hal_id": "", "message": err, "raw": ""}
        self.call("/api/deposit", {"keys": ["dupont2023rl"], "confirm": "yes"})
        self.wait_job()
        r = {x["key"]: x for x in self.call("/api/rows")[1]["rows"]}["dupont2023rl"]
        self.assertEqual(r["last_error"]["title"], "An author has no first name")
        _, d = self.call("/api/ready")
        self.assertEqual([f["key"] for f in d["failed"]], ["dupont2023rl"])
        self.assertEqual(d["ready"][0]["error"]["what"], "submission")
        self.app.deposit_fn = lambda *a, **k: {"ok": True, "status": 202, "hal_id": "", "message": "", "raw": ""}
        self.call("/api/deposit", {"keys": ["dupont2023rl"], "test": True})   # a later successful check clears it
        self.wait_job()
        r = {x["key"]: x for x in self.call("/api/rows")[1]["rows"]}["dupont2023rl"]
        self.assertIsNone(r["last_error"])


if __name__ == "__main__":
    unittest.main()
