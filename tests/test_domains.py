"""Offline tests for HAL domains (disciplines) and the interactive validation."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bib2hal import cli, domains as dom
from bib2hal.review import COLUMNS, read_csv, write_csv

FAKE_REF = {"response": {"docs": [
    {"code_s": "info", "en_domain_s": "Computer Science [cs]", "fr_domain_s": "Informatique [cs]", "level_i": 0},
    {"code_s": "info.info-ai", "en_domain_s": "Computer Science [cs]/Artificial Intelligence [cs.AI]",
     "fr_domain_s": "Informatique [cs]/Intelligence artificielle [cs.AI]", "level_i": 1},
    {"code_s": "sdv", "en_domain_s": "Life Sciences [q-bio]", "fr_domain_s": "Sciences du Vivant [q-bio]", "level_i": 0},
    {"code_s": "sdv.neu", "en_domain_s": "Life Sciences [q-bio]/Neurons and Cognition [q-bio.NC]",
     "fr_domain_s": "Sciences du Vivant [q-bio]/Neurosciences [q-bio.NC]", "level_i": 1},
    {"code_s": "sdv.bibs", "en_domain_s": "Life Sciences [q-bio]/Quantitative Methods [q-bio.QM]",
     "fr_domain_s": "Sciences du Vivant [q-bio]/Bio-informatique, Biologie systémique [q-bio.QM]", "level_i": 1},
]}}


class TestDomains(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cache = self.dir / "hal_domains.csv"
        patcher = mock.patch("bib2hal.domains.get_json", return_value=FAKE_REF)
        self.get = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_fetch_cache_search(self):
        d = dom.load_domains(cache=self.cache)
        self.assertEqual(len(d), 5)
        self.assertTrue(self.cache.exists())
        dom.load_domains(cache=self.cache)                      # second call: from cache
        self.assertEqual(self.get.call_count, 1)
        self.assertEqual([x["code"] for x in dom.search(d, "neuro")], ["sdv.neu"])
        self.assertEqual([x["code"] for x in dom.search(d, "intelligence artificielle")], ["info.info-ai"])
        self.assertEqual(dom.unknown_codes("info;sdv.xxx", d), ["sdv.xxx"])

    def test_suggest(self):
        d = dom.load_domains(cache=self.cache)
        short = ["info.info-ai", "sdv.neu", "sdv.bibs"]
        self.assertEqual(dom.suggest(short, d, "Cognition and neurons: a brain imaging study"), "sdv.neu")
        self.assertEqual(dom.suggest(short, d, "Something unrelated"), "info.info-ai")   # default = first
        self.assertEqual(dom.suggest([], d, "x"), "")

    def test_write_env(self):
        env = self.dir / ".env"
        env.write_text("BIB2HAL_ME=Dupont, Ada\nBIB2HAL_DOMAINS=info\n", encoding="utf-8")
        dom.write_env_value("BIB2HAL_DOMAINS", "sdv.neu;info.info-ai", env)
        dom.write_env_value("BIB2HAL_IDHAL", "ada-dupont", env)
        self.assertEqual(env.read_text(encoding="utf-8"),
                         "BIB2HAL_ME=Dupont, Ada\nBIB2HAL_DOMAINS=sdv.neu;info.info-ai\nBIB2HAL_IDHAL=ada-dupont\n")

    def test_pick_and_validate(self):
        cwd = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, cwd)
        with mock.patch("bib2hal.domains.CACHE", self.cache), \
                mock.patch("bib2hal.cli.dom.CACHE", self.cache), \
                mock.patch.dict(os.environ, {"BIB2HAL_DOMAINS": ""}):
            # pick: search 'neuro' -> take 1, search 'artificial' -> take 1, finish
            with mock.patch("builtins.input", side_effect=["neuro", "1", "artificial", "1", ""]):
                cli.main(["domains", "--pick"])
            self.assertIn("BIB2HAL_DOMAINS=sdv.neu;info.info-ai", (self.dir / ".env").read_text(encoding="utf-8"))

            rows = []
            for i, act in enumerate(["deposit", "deposit", "skip"]):
                r = {c: "" for c in COLUMNS}
                r.update(key=f"k{i}", title=f"Paper {i}", year="2024", action=act, hal_type="ART", domain="info")
                rows.append(r)
            csvp = self.dir / "review.csv"
            write_csv(rows, csvp)
            os.environ["BIB2HAL_DOMAINS"] = "sdv.neu;info.info-ai"
            # paper 0: choose shortlist 1,2 then accept ; paper 1: change type to COMM, accept
            with mock.patch("builtins.input", side_effect=["1,2", "", "t comm", "e conf_city=Lyon", ""]):
                cli.main(["validate", str(csvp)])
            out = {r["key"]: r for r in read_csv(csvp)}
            self.assertEqual(out["k0"]["domain"], "sdv.neu;info.info-ai")
            self.assertEqual(out["k0"]["validated"], "yes")
            self.assertEqual(out["k1"]["hal_type"], "COMM")
            self.assertEqual(out["k1"]["conf_city"], "Lyon")
            self.assertEqual(out["k2"]["validated"], "")             # 'skip' rows are not visited
            # resume: nothing left to validate, input never called
            with mock.patch("builtins.input", side_effect=AssertionError("should not ask")):
                cli.main(["validate", str(csvp)])


if __name__ == "__main__":
    unittest.main()
