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

import os

from PyInstaller.utils.hooks import collect_all

# PyInstaller resolves relative paths in a .spec file relative to the spec
# file's own directory (SPECPATH), not the CWD `pyinstaller` was invoked
# from. Anchor everything on the repo root explicitly instead of guessing.
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH is injected by PyInstaller)
SRC = os.path.join(ROOT, "src")

datas = [(os.path.join(SRC, "fullocr", "gui", "static"), "fullocr/gui/static")]
binaries = []
hiddenimports = []

# docling* / rapidocr / transformers: レイアウト・OCRモデルのコードとデータ。
#   docling_ibm_models は Docling のレイアウト/表(TableFormer)モデルの本体。動的
#   import されるうえ設定データ(json 等)を持つため、collect_all で明示収集しないと
#   凍結時に ModuleNotFoundError / データ欠落で Docling 変換が落ちる。
# webview(pywebview): Windows のバックエンド(webview.platforms.edgechromium 等)を
#   文字列で動的 import するため、collect_all で submodule を hiddenimports に
#   含めないと凍結時に ModuleNotFoundError になる。
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

# docling-parse は実行時に、自パッケージ内の pdf_resources/（グリフ表 *.dat 等）を
# 読み込む。collect_all はこのツリーの一部（例: glyphs/standard/additional.dat）を
# 取りこぼすことがあり、凍結後に docling-parse が
#   "could not load document ...: filename does not exists:
#    .../docling_parse/pdf_resources/glyphs/standard/additional.dat"
# で変換に失敗する。collect_data_files の既定挙動に頼らず、インストール済みの
# pdf_resources ツリーを os.walk で1ファイルずつ確実に同梱する（dest はパッケージ相対）。
import docling_parse  # noqa: E402 (build venv には必ず入っている)

_dp_root = os.path.dirname(docling_parse.__file__)
_res_root = os.path.join(_dp_root, "pdf_resources")
for _dirpath, _dirnames, _filenames in os.walk(_res_root):
    _rel = os.path.relpath(_dirpath, _dp_root)  # 例: pdf_resources/glyphs/standard
    for _fn in _filenames:
        datas.append((os.path.join(_dirpath, _fn), os.path.join("docling_parse", _rel)))

# Docling のレイアウト/表モデル（docling-layout-heron 等の safetensors）を同梱する。
# 実行時に HuggingFace から DL しようとして失敗する（Missing safetensors）ため、
# ワークフローがビルド前に ROOT/docling_artifacts へ prefetch しておき、それを丸ごと
# _internal/docling_artifacts に入れる（実行時 artifacts_path で参照＝オフライン化）。
_art_root = os.path.join(ROOT, "docling_artifacts")
if os.path.isdir(_art_root):
    for _dirpath, _dirnames, _filenames in os.walk(_art_root):
        _rel = os.path.relpath(_dirpath, _art_root)
        _dest = "docling_artifacts" if _rel == "." else os.path.join("docling_artifacts", _rel)
        for _fn in _filenames:
            datas.append((os.path.join(_dirpath, _fn), _dest))

# 任意依存（extra）: 入っていればバンドル、無ければ黙ってスキップ。
# google-genai(.[llm]) は LLM 構造化パス用。名前空間パッケージ google.genai。
for pkg in ("google.genai",):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:  # noqa: BLE001 - 未インストールなら収集不要
        pass

a = Analysis(
    # 凍結時のエントリは絶対 import の薄いランチャー。app.py を直接エントリにすると
    # __main__ 実行になり `from .server import app` が相対 import で失敗する。
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
