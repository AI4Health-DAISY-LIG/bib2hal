# PyInstaller recipe: one self-contained executable per OS (built by .github/workflows/release.yml).
# Local build:  pip install pyinstaller keyring pillow  &&  pyinstaller packaging/bib2hal.spec
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "bib2hal", "static"), os.path.join("bib2hal", "static"))],
    hiddenimports=["bib2hal.ui"] + collect_submodules("keyring.backends"),
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="bib2hal",
    console=True,                      # a small window shows where files are kept; closing it stops bib2hal
    upx=False,
    icon=os.path.join(SPECPATH, "icon.png"),   # converted to .ico / .icns by PyInstaller (needs Pillow)
)
