"""PyInstaller entry script (see bib2hal.spec)."""
import sys

from bib2hal.app import main

sys.exit(main())
