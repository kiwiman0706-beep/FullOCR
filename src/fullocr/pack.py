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


def _load_meta(cfg: PipelineConfig, book: str):
    ir = cfg.work_dir / book / "ir.json"
    if ir.exists():
        try:
            return Document.load(ir).meta
        except Exception:  # noqa: BLE001
            return None
    return None


def _find_pdf(out_book: Path, pdf_name: str | None) -> Path | None:
    if pdf_name and (out_book / pdf_name).exists():
        return out_book / pdf_name
    hits = sorted(out_book.glob("*_text.pdf")) or sorted(out_book.glob("*.pdf"))
    return hits[0] if hits else None


def pack_book(cfg: PipelineConfig, book: str, *, pdf_mode: str = "embed") -> dict:
    """out/<book>/ を <book>.jisui にまとめる。pdf_mode: embed|sidecar|none。"""
    out_book = cfg.out_dir / book
    if not (out_book / "00_index.md").exists():
        raise FileNotFoundError(f"変換結果がありません: {out_book}")

    meta = _load_meta(cfg, book)
    pdf_name = meta.pdf_name if meta else None
    pdf_file = _find_pdf(out_book, pdf_name)
    if pdf_file and not pdf_name:
        pdf_name = pdf_file.name

    sidecar_name = f"{book}.pdf"
    if not pdf_file:
        pdf_entry: dict = {"mode": "none"}
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
        "page_anchors": "fullocr-1",
        "generator": f"fullocr {__version__}",
        "created": time.strftime("%Y-%m-%d"),
    }
    if (out_book / "_digest.md").exists():
        manifest["digest"] = "theta-digest-1"
    if meta and meta.isbn:
        manifest["isbn"] = meta.isbn
    if meta and meta.publisher:
        manifest["publisher"] = meta.publisher

    jisui_path = cfg.out_dir / f"{book}.jisui"
    embed = pdf_entry["mode"] == "embedded"
    with zipfile.ZipFile(jisui_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("jisui.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for f in sorted(out_book.rglob("*")):
            if f.is_dir():
                continue
            if pdf_file and f == pdf_file and not embed:
                continue  # 非同梱モードでは PDF を入れない
            z.write(f, f.relative_to(out_book).as_posix())

    return {
        "book": book,
        "jisui": str(jisui_path),
        "pdf_mode": pdf_entry["mode"],
        "sidecar": str(cfg.out_dir / sidecar_name) if pdf_entry["mode"] == "sidecar" else None,
    }
