"""Offline tests for the downloadable-app entry point."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bib2hal import app


class TestApp(unittest.TestCase):
    def test_workdir(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"BIB2HAL_HOME": d}):
            self.assertEqual(app.default_workdir(), Path(d))
        with mock.patch.dict(os.environ, {"BIB2HAL_HOME": ""}):
            self.assertEqual(app.default_workdir().name, "bib2hal")

    def test_double_click_opens_ui_in_workdir(self):
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"BIB2HAL_HOME": str(Path(d) / "w")}), \
                mock.patch("bib2hal.ui.run", return_value=0) as run:
            self.assertEqual(app.main([]), 0)
            run.assert_called_once()
            self.assertEqual(Path.cwd().resolve(), (Path(d) / "w").resolve())

    def test_arguments_go_to_cli(self):
        with mock.patch("bib2hal.cli.main", return_value=7) as m:
            self.assertEqual(app.main(["whoami"]), 7)
            m.assert_called_once_with(["whoami"])


if __name__ == "__main__":
    unittest.main()
