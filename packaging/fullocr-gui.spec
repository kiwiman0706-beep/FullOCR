# PyInstaller spec for the FullOCR GUI (Windows exe).
#
# Builds a --onedir distribution (not --onefile): Docling pulls in torch +
# transformers, and PyInstaller onefile mode has to unpack that on every
# launch, which is slow and fragile for ML-heavy dependency trees. A folder
# you double-click the .exe inside of is the practical shape for this.
#
# Run from the repo root:
#   pyinstaller packaging/fullocr-gui.spec
#
# NDLOCR-Lite is NOT bundled by this spec — it's a separately licensed,
# separately built companion executable (see .github/workflows/build-windows.yml),
# placed in dist/fullocr-gui/ndlocr-lite/ alongside this app.

from PyInstaller.utils.hooks import collect_all

datas = [("src/fullocr/gui/static", "fullocr/gui/static")]
binaries = []
hiddenimports = []

for pkg in ("docling", "docling_core", "docling_parse", "rapidocr", "transformers"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["src/fullocr/gui/app.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="fullocr-gui",
    console=False,
    onefile=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="fullocr-gui",
)
