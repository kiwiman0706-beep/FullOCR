# PyInstaller spec for the FullOCR GUI (macOS .app, Apple Silicon).
#
# Same Analysis as the Windows spec, but wraps the onedir COLLECT in a BUNDLE
# so double-clicking produces a real FullOCR.app. Built on a macOS (arm64)
# GitHub runner (see .github/workflows/build-macos.yml).
#
#   pyinstaller packaging/fullocr-gui-mac.spec
#
# NDLOCR-Lite ships as a separately built companion, copied into
# FullOCR.app/Contents/MacOS/ndlocr-lite/ by the workflow. server._frozen_dir()
# (= Contents/MacOS) resolves it at runtime; outputs go next to the .app.

import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH injected by PyInstaller)
SRC = os.path.join(ROOT, "src")

datas = [(os.path.join(SRC, "fullocr", "gui", "static"), "fullocr/gui/static")]
binaries = []
hiddenimports = []

# docling* / rapidocr / transformers: レイアウト・OCRモデルのコードとデータ。
# webview(pywebview): macOS は WKWebView(pyobjc) を動的 import する。
for pkg in (
    "docling",
    "docling_core",
    "docling_parse",
    "docling_ibm_models",
    "rapidocr",
    "transformers",
    "webview",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 任意依存: google-genai(.[llm])。入っていれば同梱。
for pkg in ("google.genai",):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:  # noqa: BLE001
        pass

a = Analysis(
    [os.path.join(ROOT, "packaging", "gui_entry.py")],
    pathex=[SRC],
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

app = BUNDLE(
    coll,
    name="FullOCR.app",
    icon=None,
    bundle_identifier="com.fullocr.gui",
    info_plist={
        "NSHighResolutionCapable": True,
        # ネット越しにモデルをDLする（Docling/Gemini）。ATSでlocalhostも許可。
        "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
    },
)
