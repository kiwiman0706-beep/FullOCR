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
    "latex2mathml",
    "webview",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# latex2mathml は数式変換時に自パッケージ内 unimathsymbols.txt を読む。collect_all が
# 取りこぼすと凍結後に FileNotFoundError(.../latex2mathml/unimathsymbols.txt) で落ちるため、
# データファイルを os.walk で確実に同梱する（docling がレイアウト解析で間接利用）。
try:
    import latex2mathml  # noqa: E402

    _l2m_root = os.path.dirname(latex2mathml.__file__)
    _l2m_parent = os.path.dirname(_l2m_root)
    for _dirpath, _dirnames, _filenames in os.walk(_l2m_root):
        _rel = os.path.relpath(_dirpath, _l2m_parent)
        for _fn in _filenames:
            if not _fn.endswith(".py"):
                datas.append((os.path.join(_dirpath, _fn), _rel))
except Exception:  # noqa: BLE001
    pass

# docling-parse は実行時に自パッケージ内 pdf_resources/（グリフ表 *.dat 等）を読む。
# collect_all がこのツリーの一部を取りこぼし、凍結後に
#   "docling-parse could not load document ...: filename does not exists:
#    .../docling_parse/pdf_resources/glyphs/standard/additional.dat"
# で失敗する。インストール済みの pdf_resources を os.walk で1ファイルずつ確実に同梱する。
import docling_parse  # noqa: E402 (build venv には必ず入っている)

_dp_root = os.path.dirname(docling_parse.__file__)
_res_root = os.path.join(_dp_root, "pdf_resources")
for _dirpath, _dirnames, _filenames in os.walk(_res_root):
    _rel = os.path.relpath(_dirpath, _dp_root)
    for _fn in _filenames:
        datas.append((os.path.join(_dirpath, _fn), os.path.join("docling_parse", _rel)))

# Docling のレイアウト/表モデル（safetensors）をビルド前 prefetch したものを同梱。
# 実行時に HF から DL して失敗するのを避け、artifacts_path でオフライン参照する。
_art_root = os.path.join(ROOT, "docling_artifacts")
if os.path.isdir(_art_root):
    for _dirpath, _dirnames, _filenames in os.walk(_art_root):
        _rel = os.path.relpath(_dirpath, _art_root)
        _dest = "docling_artifacts" if _rel == "." else os.path.join("docling_artifacts", _rel)
        for _fn in _filenames:
            datas.append((os.path.join(_dirpath, _fn), _dest))

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
    icon=os.path.join(SPECPATH, "icon.icns"),  # noqa: F821 (SPECPATH injected by PyInstaller)
    bundle_identifier="com.fullocr.gui",
    info_plist={
        "NSHighResolutionCapable": True,
        # Finder/Dock に出す表示名（バンドル名 FullOCR.app は変えずワークフロー維持）。
        "CFBundleName": "Theta.FullOCR",
        "CFBundleDisplayName": "Theta.FullOCR",
        # ネット越しにモデルをDLする（Docling/Gemini）。ATSでlocalhostも許可。
        "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
    },
)
