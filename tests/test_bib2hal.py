"""Offline tests: network calls are replaced by canned responses.

Run:  python -m unittest discover -s tests -v
"""
import csv
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from bib2hal import cli, crossref, hal
from bib2hal.bibtex import latex_to_unicode, parse_bibtex, split_authors
from bib2hal.review import read_csv
from bib2hal.tei import TEI_NS, build_package, build_tei

HERE = Path(__file__).parent
NS = {"t": TEI_NS}

CROSSREF_ITEM = {
    "DOI": "10.1016/j.neuroimage.2020.116000",
    "type": "journal-article",
    "title": ["Deep learning for fMRI laterality: a étude of 100 subjects"],
    "author": [{"given": "Sandrine", "family": "Muller", "ORCID": "http://orcid.org/0000-0002-1825-0097"},
               {"given": "Jane", "family": "Doe"}, {"given": "John", "family": "Smith"}],
    "container-title": ["NeuroImage"],
    "issued": {"date-parts": [[2020, 3]]},
    "volume": "210", "page": "116-125", "publisher": "Elsevier BV", "ISSN": ["1053-8119"],
    "abstract": "<jats:p>We study laterality.</jats:p>",
}
CONF_ITEM = {
    "DOI": "10.1109/aime.2023.42", "type": "proceedings-article",
    "title": ["Reinforcement learning on biomedical knowledge graphs"],
    "author": [{"given": "Sandrine", "family": "Muller"}, {"given": "François", "family": "Martin"}],
    "container-title": ["2023 Conference on AI in Medicine"],
    "issued": {"date-parts": [[2023]]},
    "event": {"name": "AIMed 2023", "location": "Grenoble, France", "start": {"date-parts": [[2023, 6, 12]]}},
}


def fake_get_json(url, params=None, **_):
    params = params or {}
    if url.startswith(crossref.CROSSREF + "/"):          # lookup by DOI
        return None
    if url == crossref.CROSSREF:                         # bibliographic search
        q = params.get("query.bibliographic", "").lower()
        if "deep learning" in q:
            return {"message": {"items": [CROSSREF_ITEM]}}
        if "reinforcement" in q:
            return {"message": {"items": [CONF_ITEM]}}
        return {"message": {"items": []}}
    if url == hal.SEARCH:
        q = params["q"]
        if "10.1016/j.neuroimage.2020.116000" in q:      # already in HAL, not linked
            return {"response": {"docs": [{"halId_s": "hal-01234567", "uri_s": "https://hal.science/hal-01234567",
                                           "title_s": [CROSSREF_ITEM["title"][0]], "authIdHal_s": []}]}}
        if "lateralisation" in q:                       # thesis already linked
            return {"response": {"docs": [{"halId_s": "tel-0099", "uri_s": "https://theses.hal.science/tel-0099",
                                           "title_s": ["Latéralisation du langage chez l'enfant"],
                                           "authIdHal_s": ["sandrine-muller"]}]}}
        return {"response": {"docs": []}}
    raise AssertionError(f"unexpected URL {url}")


class TestBibtex(unittest.TestCase):
    def test_latex(self):
        self.assertEqual(latex_to_unicode(r"Fran{\c{c}}ois M{\"u}ller {\'e}t\'e \& co"), "François Müller été & co")

    def test_parse(self):
        e = parse_bibtex((HERE / "sample.bib").read_text(encoding="utf-8"))
        self.assertEqual(len(e), 6)
        self.assertEqual(e[0]["ENTRYTYPE"], "article")
        self.assertEqual(e[3]["year"], "2015")
        self.assertEqual(latex_to_unicode(e[3]["title"]), "Latéralisation du langage chez l'enfant")

    def test_authors(self):
        a = split_authors("Muller, Sandrine and Jane Doe and others")
        self.assertEqual(a, [{"family": "Muller", "given": "Sandrine"}, {"family": "Doe", "given": "Jane"}])

    def test_location(self):
        self.assertEqual(crossref.split_location("Grenoble, France"), ("Grenoble", "FR"))
        self.assertEqual(crossref.split_location("Honolulu, HI, USA"), ("Honolulu", "US"))


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {
            "BIB2HAL_ME": "Muller, Sandrine", "BIB2HAL_IDHAL": "sandrine-muller",
            "BIB2HAL_STRUCTS": "1234", "BIB2HAL_STRUCTS_SINCE": "2021", "BIB2HAL_DOMAIN": "info"})
        self.env.start()
        p1 = mock.patch("bib2hal.crossref.get_json", side_effect=fake_get_json)
        p2 = mock.patch("bib2hal.hal.get_json", side_effect=fake_get_json)
        p1.start(), p2.start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _review(self):
        out = self.dir / "review.csv"
        cli.main(["review", str(HERE / "sample.bib"), "-o", str(out)])
        return out, {r["key"]: r for r in read_csv(out)}

    def test_review_actions(self):
        _, rows = self._review()
        self.assertEqual(rows["muller2020deep"]["action"], "attach")          # in HAL, not linked
        self.assertEqual(rows["muller2020deep"]["doi"], "10.1016/j.neuroimage.2020.116000")
        self.assertEqual(rows["muller2015these"]["action"], "skip")           # in HAL, linked
        self.assertEqual(rows["muller2020deep_dup"]["action"], "duplicate")
        self.assertEqual(rows["homonym2010"]["action"], "check")              # not me
        self.assertEqual(rows["muller2024preprint"]["hal_type"], "UNDEFINED")
        conf = rows["muller2023kg"]
        self.assertEqual(conf["action"], "deposit")
        self.assertEqual((conf["hal_type"], conf["conf_city"], conf["conf_country"], conf["conf_date"]),
                         ("COMM", "Grenoble", "FR", "2023-06-12"))
        self.assertEqual(conf["my_structs"], "1234")                          # >= STRUCTS_SINCE
        self.assertEqual(conf["missing"], "")
        self.assertEqual(rows["muller2020deep"]["my_structs"], "")            # 2020 < since

    def test_tei_conference(self):
        _, rows = self._review()
        xml = build_tei(rows["muller2023kg"], idhal="sandrine-muller")
        root = ET.fromstring(xml)
        self.assertEqual(root.find(".//t:classCode[@scheme='halTypology']", NS).get("n"), "COMM")
        self.assertEqual(root.find(".//t:meeting/t:settlement", NS).text, "Grenoble")
        self.assertEqual(root.find(".//t:meeting/t:country", NS).get("key"), "FR")
        aff = root.findall(".//t:analytic/t:author", NS)
        self.assertEqual(aff[0].find("t:affiliation", NS).get("ref"), "#struct-1234")
        self.assertIsNone(aff[1].find("t:affiliation", NS))
        self.assertEqual(aff[0].find("t:idno[@type='idhal']", NS).text, "sandrine-muller")
        self.assertEqual(root.find(".//t:biblStruct/t:idno[@type='doi']", NS).text, "10.1109/aime.2023.42")

    def test_tei_with_pdf_is_zip(self):
        _, rows = self._review()
        pdf = self.dir / "paper v2.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        row = dict(rows["muller2023kg"], pdf=str(pdf))
        payload, is_zip = build_package(row)
        self.assertTrue(is_zip)
        import io, zipfile
        z = zipfile.ZipFile(io.BytesIO(payload))
        self.assertEqual(sorted(z.namelist()), ["meta.xml", "paper_v2.pdf"])
        meta = ET.fromstring(z.read("meta.xml"))
        self.assertEqual(meta.find(".//t:edition/t:ref", NS).get("target"), "paper_v2.pdf")

    def test_deposit_idempotent(self):
        out, _ = self._review()
        calls = []

        def fake_sword(payload, is_zip, login, pwd, filename="meta.xml", test=False):
            calls.append(login)
            return {"ok": True, "status": 201, "hal_id": "hal-04000001", "message": "", "raw": ""}

        log = self.dir / "log.csv"
        from bib2hal import credentials as cred
        with mock.patch.object(cred, "_backend", None), mock.patch.object(cred, "_backend_checked", True), \
                mock.patch.object(cred, "_session", {"hal": ("x", "y")}), \
                mock.patch("bib2hal.cli.sword_deposit", side_effect=fake_sword):
            cli.main(["deposit", str(out), "--log", str(log), "--yes"])
            cli.main(["deposit", str(out), "--log", str(log), "--yes"])          # second run: nothing new
        self.assertEqual(calls, ["x"])
        with open(log, encoding="utf-8-sig") as f:
            self.assertEqual(len(list(csv.DictReader(f, delimiter=";"))), 1)


if __name__ == "__main__":
    unittest.main()
