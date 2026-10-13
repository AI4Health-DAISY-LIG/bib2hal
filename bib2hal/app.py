"""Entry point of the downloadable application (Windows .exe, macOS, Linux).

Double-clicked (no arguments): opens the web interface, working in a fixed per-user folder
(Documents/bib2hal by default) so that .env, review.csv and logs are always in the same place.
With arguments it behaves exactly like the `bib2hal` command line, e.g.  bib2hal.exe review scholar.bib
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import __version__, cli, ui
from .util import load_env


def default_workdir() -> Path:
    """BIB2HAL_HOME if set, else ~/Documents/bib2hal (or ~/bib2hal when there is no Documents folder)."""
    env = os.environ.get("BIB2HAL_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "bib2hal"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        return cli.main(argv)
    cli._safe_console()
    wd = default_workdir()
    wd.mkdir(parents=True, exist_ok=True)
    os.chdir(wd)
    load_env(".env")
    print(f"bib2hal {__version__} — AI4Health @ DAISY\n"
          f"Your files are kept in: {wd}\n"
          "The interface opens in your web browser. Keep this window open while you use it;\n"
          "close it (or press Ctrl+C) to stop bib2hal.\n")
    return ui.run()


if __name__ == "__main__":
    sys.exit(main())
