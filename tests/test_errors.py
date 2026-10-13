"""Offline tests: explanations of HAL errors."""
import unittest

from bib2hal.errors import explain


class TestErrors(unittest.TestCase):
    def test_known_messages(self):
        self.assertEqual(explain("Element '{x}surname': This element is not expected. Expected is ( {x}forename ).")["title"],
                         "An author has no first name")
        self.assertEqual(explain("Element '{x}imprint': This element is not expected. Expected is ( {x}authority ).")["title"],
                         "Thesis details missing")
        e = explain('not understood {"duplicate-entry":{"hal-05744213":{"doi":"1.0"}}}')
        self.assertEqual(e["title"], "Already in HAL")
        self.assertIn("hal-05744213", e["help"])
        self.assertEqual(explain("", 401)["title"], "Login refused")
        self.assertEqual(explain("something odd", 500)["title"], "HAL refused this record")


if __name__ == "__main__":
    unittest.main()
