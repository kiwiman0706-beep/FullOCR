"""`.jisui` コンテナの書き出し（自炊本を1ファイルにまとめる）。

`.jisui` は ZIP で、中身は out/<book>/ 一式（Obsidian Vault 形式の MD 群・attachments・
任意で _digest.md 等）＋ マニフェスト `jisui.json`。原典PDFは3モード:
  embed   … ZIP に同梱（1ファイルで完結・大きい）
  sidecar … ZIP に入れず、.jisui と同じ場所に <book>.pdf を置く（.jisui は小さい）
  none    … PDF を含めない（ページリンクは印刷ページ表示のみ）

ページジャンプは page-anchor-spec（#page=N＝物理ページ）を再利用。ビューワ
（Theta.Book.Hatch）はマニフェストの pdf.mode を見て同梱 or sidecar を開く。
仕様: docs/jisui-format-spec.md。
"""
from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path

from . import __version__
from .config import PipelineConfig
from .ir import Document

JISUI_FORMAT = "jisui-1"
# EPUB 同様、ZIP 先頭に無圧縮で置く種別判別用エントリと、その内容（MIME）。
MIMETYPE = "application/x-jisui"
MANIFEST_NAME = "theta-book.json"

# すでに圧縮済みの形式は ZIP で再圧縮しない（STORED）。PDF/画像/EPUB 等。
# テキスト（.md/.json/.txt）だけ DEFLATE で縮める。
_STORE_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".epub", ".zip"}


def _ctype(path: Path) -> int:
    return zipfile.ZIP_STORED if path.suffix.lower() in _STORE_EXT else zipfile.ZIP_DEFLATED


def _load_doc_meta(cfg: PipelineConfig, book: str):
    """(meta, page_offset) を work/<book>/ir.json から読む。無ければ (None, None)。"""
    ir = cfg.work_dir / book / "ir.json"
    if not ir.exists():
        return None, None
    try:
        doc = Document.load(ir)
    except Exception:  # noqa: BLE001
        return None, None
    from .render.markdown import _page_offset
    return doc.meta, _page_offset(doc)


def _declared_anchor_scheme(index_md: Path) -> str | None:
    """00_index.md の front-matter にある page_anchors の値（無ければ None）。"""
    try:
        text = index_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        key, _, val = line.partition(":")
        if key.strip() == "page_anchors" and val.strip():
            return val.strip()
    return None


def _find_pdf(out_book: Path, pdf_name: str | None) -> Path | None:
    if pdf_name and (out_book / pdf_name).exists():
        return out_book / pdf_name
    hits = sorted(out_book.glob("*_text.pdf")) or sorted(out_book.glob("*.pdf"))
    return hits[0] if hits else None


def _is_ours(jisui_path: Path) -> bool:
    """既存の .jisui が FullOCR の書き出したものか（他人のファイルを上書きしないため）。"""
    try:
        with zipfile.ZipFile(jisui_path) as z:
            names = z.namelist()
            name = MANIFEST_NAME if MANIFEST_NAME in names else "jisui.json"
            man = json.loads(z.read(name))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return False
    return str(man.get("generator", "")).startswith("fullocr")


def pack_book(
    cfg: PipelineConfig, book: str, *, pdf_mode: str = "embed", source_pdf: Path | str | None = None
) -> dict:
    """out/<book>/ を <book>.jisui にまとめる。pdf_mode: embed|sidecar|none|source。

    source … 原本PDFの隣に「原本と同じ名前.jisui」を書き、原文PDFは原本そのものを
    sidecar として参照する（コピーしない）。Theta Book Hatch は同じフォルダ・同じ名前の
    X.pdf と X.jisui を1冊（PDF＋JISUI）にまとめ、MD⇄原本PDFを同じページで行き来できる。
    原本の場所は source_pdf（省略時は IR の DocMeta.source_pdf）。
    """
    out_book = cfg.out_dir / book
    if not (out_book / "00_index.md").exists():
        raise FileNotFoundError(f"変換結果がありません: {out_book}")

    meta, page_offset = _load_doc_meta(cfg, book)
    pdf_name = meta.pdf_name if meta else None
    pdf_file = _find_pdf(out_book, pdf_name)
    if pdf_file and not pdf_name:
        pdf_name = pdf_file.name

    sidecar_name = f"{book}.pdf"
    jisui_path = cfg.out_dir / f"{book}.jisui"
    if pdf_mode == "source":
        src = Path(source_pdf or (meta.source_pdf if meta else "") or "")
        if not src.name or not src.is_file():
            raise FileNotFoundError(f"原本PDFが見つかりません: {src}")
        jisui_path = src.with_suffix(".jisui")
        if jisui_path.exists() and not _is_ours(jisui_path):
            raise FileExistsError(f"FullOCR 以外の .jisui があるので上書きしません: {jisui_path}")
        # 原文PDFは原本（ページは NDL の透明PDFと1対1で同じ）。MD リンクの論理名は据え置き。
        pdf_entry: dict = {"mode": "sidecar", "name": pdf_name or src.name, "sidecar": src.name}
    elif not pdf_file:
        pdf_entry = {"mode": "none"}
    elif pdf_mode == "embed":
        pdf_entry = {"mode": "embedded", "name": pdf_name}
    elif pdf_mode == "sidecar":
        shutil.copy2(pdf_file, cfg.out_dir / sidecar_name)
        pdf_entry = {"mode": "sidecar", "name": pdf_name, "sidecar": sidecar_name}
    else:  # none
        pdf_entry = {"mode": "none", "name": pdf_name}

    manifest = {
        "format": JISUI_FORMAT,
        "title": (meta.title if meta else book) or book,
        "author": meta.author if meta else None,
        "entry": "00_index.md",
        "pdf": pdf_entry,
        "generator": f"fullocr {__version__}",
        "created": time.strftime("%Y-%m-%d"),
    }
    # MD 側に実際にアンカーを埋めた時だけ宣言する（00_index.md の front-matter が正）。
    # 以前は常に fullocr-1 を宣言しており、page_anchors=False で変換した本でも
    # ビューワ（jisui.js は manifest で front-matter を補完する）が対応本と誤認していた。
    anchors = _declared_anchor_scheme(out_book / "00_index.md")
    if anchors:
        manifest["page_anchors"] = anchors
    if page_offset is not None:
        manifest["page_offset"] = page_offset
    if (out_book / "_digest.md").exists():
        manifest["digest"] = "theta-digest-1"
    if meta and meta.isbn:
        manifest["isbn"] = meta.isbn
    if meta and meta.publisher:
        manifest["publisher"] = meta.publisher

    embed = pdf_entry["mode"] == "embedded"
    # アーカイブ既定は DEFLATE（テキスト向け）。各ファイルは _ctype で切替し、
    # すでに圧縮済みの PDF/画像は STORED（無圧縮）で入れて再圧縮の無駄を避ける。
    # NAS 上に直接書くので、一時ファイルに書いてから置き換える（途中で切れても壊さない）。
    tmp_path = jisui_path.with_name(jisui_path.name + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as z:
        # EPUB 同様、先頭に無圧縮の mimetype を置く（中身を開かず種別判別できる）。
        z.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)
        z.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
        for f in sorted(out_book.rglob("*")):
            if f.is_dir():
                continue
            if pdf_file and f == pdf_file and not embed:
                continue  # 非同梱モードでは PDF を入れない
            z.write(f, f.relative_to(out_book).as_posix(), compress_type=_ctype(f))
    tmp_path.replace(jisui_path)

    return {
        "book": book,
        "jisui": str(jisui_path),
        "pdf_mode": pdf_entry["mode"],
        "sidecar": (str(jisui_path.with_name(pdf_entry["sidecar"]))
                    if pdf_entry["mode"] == "sidecar" else None),
    }
