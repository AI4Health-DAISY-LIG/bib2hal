"""Fixes from the first real deposits: HAL schema errors and duplicates (offline)."""
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from bib2hal import hal
from bib2hal.review import COLUMNS, missing_fields
from bib2hal.tei import TEI_NS, build_tei

NS = {"t": TEI_NS}


def row(**kw):
    r = {c: "" for c in COLUMNS}
    r.update(action="deposit", hal_type="ART", title="T", year="2022", venue="J", domain="info", language="en",
             me_position="1", my_structs="1", authors="Dupont, Ada")
    r.update(kw)
    return r


class TestHalFeedback(unittest.TestCase):
    def test_consortium_author_left_out(self):
        r = row(authors="Nguyen, Linh; The Example Research Consortium; Dupont, Ada", me_position="3")
        self.assertEqual(missing_fields(r), [])
        root = ET.fromstring(build_tei(r))
        names = [a.find("t:persName/t:surname", NS).text for a in root.findall(".//t:analytic/t:author", NS)]
        self.assertEqual(names, ["Nguyen", "Dupont"])
        for a in root.findall(".//t:analytic/t:author", NS):          # every person has a forename
            self.assertIsNotNone(a.find("t:persName/t:forename", NS))
        me = root.findall(".//t:analytic/t:author", NS)[1]
        self.assertEqual(me.find("t:affiliation", NS).get("ref"), "#struct-1")   # still the right person

    def test_person_without_given_name_is_flagged(self):
        self.assertIn("authors: 'Smith' has no given name", missing_fields(row(authors="Dupont, Ada; Smith")))

    def test_thesis_needs_supervisor(self):
        r = row(hal_type="THESE", venue="", institution="Université de Lausanne")
        self.assertIn("supervisor", missing_fields(r))
        r["supervisor"] = "Jane Doe; John Smith"
        self.assertEqual(missing_fields(r), [])
        mo = ET.fromstring(build_tei(r)).find(".//t:monogr", NS)
        tags = [(c.tag.split("}")[1], c.get("type")) for c in mo]
        self.assertEqual(tags[:3], [("authority", "institution"), ("authority", "supervisor"), ("authority", "supervisor")])
        self.assertEqual(tags[3][0], "imprint")

    def test_duplicate_entry_is_recognised(self):
        body = 'ERROR Some parameters sent with the request were not understood {"duplicate-entry":{"hal-05744213":{"doi":"1.0"}}}'
        with mock.patch("bib2hal.hal.request", return_value=(400, body)):
            res = hal.sword_deposit(b"<TEI/>", False, "me", "pw")
        self.assertFalse(res["ok"])
        self.assertEqual(res["duplicate_of"], "hal-05744213")
        self.assertIn("Already in HAL as hal-05744213", res["message"])


if __name__ == "__main__":
    unittest.main()
