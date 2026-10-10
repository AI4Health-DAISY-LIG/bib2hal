"""Offline tests: credential storage and the HAL login check."""
import os
import unittest
from pathlib import Path
from unittest import mock

from bib2hal import credentials as cred, hal, ui


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self.store = {}
        store = self.store

        class Fake:
            label = "Fake"
            get = staticmethod(lambda t: store.get(t))
            set = staticmethod(lambda t, v: store.__setitem__(t, v))
            delete = staticmethod(lambda t: store.pop(t, None))

        for name, val in (("_backend", Fake()), ("_backend_checked", True), ("_session", {})):
            p = mock.patch.object(cred, name, val)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {"HAL_LOGIN": "", "HAL_PASSWORD": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_session_and_keychain(self):
        self.assertIsNone(cred.get())
        cred.set_session("a", "b")
        self.assertEqual((cred.get(), cred.source()), (("a", "b"), "session"))
        self.assertTrue(cred.remember("a", "b"))
        self.assertEqual(cred.source(), "keychain")
        cred._session.clear()
        self.assertEqual(cred.get(), ("a", "b"))                        # back from the keychain
        cred.forget()
        self.assertIsNone(cred.get())
        self.assertEqual(self.store, {})

    def test_never_reads_env(self):
        with mock.patch.dict(os.environ, {"HAL_LOGIN": "x", "HAL_PASSWORD": "y"}):
            self.assertIsNone(cred.get())

    def test_no_keychain(self):
        with mock.patch.object(cred, "_backend", None):
            self.assertFalse(cred.remember("a", "b"))
            self.assertEqual(cred.keychain_label(), "")
            cred.set_session("a", "b")
            self.assertEqual(cred.get(), ("a", "b"))                    # memory only

    def test_check_login(self):
        for status, ok in ((200, True), (401, False), (0, False), (500, False)):
            with mock.patch("bib2hal.hal.request", return_value=(status, "x")) as r:
                res = hal.check_login("me", "pw")
            self.assertEqual(res["ok"], ok)
            url, kw = r.call_args[0][0], r.call_args[1]
            self.assertEqual(url, "https://api.archives-ouvertes.fr/sword/servicedocument")
            self.assertTrue(kw["headers"]["Authorization"].startswith("Basic "))

    def test_xtest_header(self):
        with mock.patch("bib2hal.hal.request", return_value=(202, "<entry/>")) as r:
            hal.sword_deposit(b"<TEI/>", False, "me", "pw", test=True)
        self.assertEqual(r.call_args[0][2]["X-test"], "1")
        self.assertEqual(r.call_args[0][0], "https://api.archives-ouvertes.fr/sword/hal/")

    def test_drop_env_keys(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("BIB2HAL_ME=Muller, S\nHAL_LOGIN=me\nHAL_PASSWORD=secret\n", encoding="utf-8")
            ui._drop_env_keys(env, ("HAL_LOGIN", "HAL_PASSWORD"))
            self.assertEqual(env.read_text(encoding="utf-8"), "BIB2HAL_ME=Muller, S\n")


if __name__ == "__main__":
    unittest.main()
