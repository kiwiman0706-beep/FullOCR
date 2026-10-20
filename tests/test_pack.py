"""`.jisui` コンテナ書き出しの検証（ZIP＋マニフェスト、PDF 3モード）。"""

from __future__ import annotations

import json
import zipfile

from fullocr import pack
from fullocr.config import PipelineConfig
from fullocr.ir import BBox, Block, BlockType, DocMeta, Document, Page


def _make_book(tmp_path, book="テスト本", with_pdf=True, with_digest=False):
    cfg = PipelineConfig()
    cfg.out_dir = tmp_path / "out"
    cfg.work_dir = tmp_path / "work"
    out_book = cfg.out_dir / book
    (out_book / "attachments").mkdir(parents=True)
    (out_book / "00_index.md").write_text("# 目次\n", encoding="utf-8")
    (out_book / "本文.md").write_text("本文です。", encoding="utf-8")
    (out_book / "attachments" / "p1.png").write_bytes(b"\x89PNG\r\n")
    if with_digest:
        (out_book / "_digest.md").write_text("<!-- theta:digest -->\n# 要約", encoding="utf-8")
    pdf_name = f"{book}_text.pdf"
    if with_pdf:
        (out_book / pdf_name).write_bytes(b"%PDF-1.4\n")
    # work/<book>/ir.json（メタ＋ノンブルで page_offset=8 を出す）
    (cfg.work_dir / book).mkdir(parents=True)
    pages = []
    for i, printed in enumerate(["1", "2", "3"], start=9):  # pdf 9..11 − printed 1..3 = 8
        p = Page(number=i, width=100, height=140, blocks=[
            Block(id=f"f{i}", bbox=BBox(x0=40, y0=130, x1=60, y1=138),
                  type=BlockType.PAGE_FOOTER, text=printed, reading_order=0),
        ])
        pages.append(p)
    doc = Document(meta=DocMeta(title="テスト本のタイトル", author="著者", pdf_name=pdf_name,
                                isbn="9784000000000"), pages=pages)
    doc.save(cfg.work_dir / book / "ir.json")
    return cfg, book, pdf_name


def test_pack_embed_includes_pdf_and_manifest(tmp_path):
    cfg, book, pdf_name = _make_book(tmp_path, with_digest=True)
    r = pack.pack_book(cfg, book, pdf_mode="embed")
    assert r["pdf_mode"] == "embedded"
    z = zipfile.ZipFile(r["jisui"])
    names = z.namelist()
    # EPUB 同様、先頭は無圧縮の mimetype
    assert names[0] == "mimetype"
    assert z.read("mimetype").decode() == pack.MIMETYPE
    assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    assert "theta-book.json" in names
    assert "00_index.md" in names
    assert "attachments/p1.png" in names
    assert pdf_name in names  # 同梱されている
    man = json.loads(z.read("theta-book.json"))
    assert man["format"] == "jisui-1"
    assert man["title"] == "テスト本のタイトル"
    assert man["pdf"] == {"mode": "embedded", "name": pdf_name}
    assert man["page_anchors"] == "fullocr-1"
    assert man["page_offset"] == 8
    assert man["digest"] == "theta-digest-1"
    assert man["isbn"] == "9784000000000"


def test_pack_sidecar_excludes_pdf_and_writes_sidecar(tmp_path):
    cfg, book, pdf_name = _make_book(tmp_path)
    r = pack.pack_book(cfg, book, pdf_mode="sidecar")
    assert r["pdf_mode"] == "sidecar"
    z = zipfile.ZipFile(r["jisui"])
    assert pdf_name not in z.namelist()  # 同梱しない
    sidecar = cfg.out_dir / f"{book}.pdf"
    assert sidecar.exists()  # .jisui の隣に <book>.pdf
    man = json.loads(z.read("theta-book.json"))
    assert man["pdf"]["mode"] == "sidecar"
    assert man["pdf"]["sidecar"] == f"{book}.pdf"


def test_pack_none_omits_pdf(tmp_path):
    cfg, book, pdf_name = _make_book(tmp_path)
    r = pack.pack_book(cfg, book, pdf_mode="none")
    z = zipfile.ZipFile(r["jisui"])
    assert pdf_name not in z.namelist()
    assert not (cfg.out_dir / f"{book}.pdf").exists()
    man = json.loads(z.read("theta-book.json"))
    assert man["pdf"]["mode"] == "none"


def test_embedded_pdf_and_images_are_stored_text_is_deflated(tmp_path):
    # すでに圧縮済みの PDF/PNG は無圧縮(STORED)、テキストは DEFLATE で入る。
    cfg, book, pdf_name = _make_book(tmp_path)
    r = pack.pack_book(cfg, book, pdf_mode="embed")
    z = zipfile.ZipFile(r["jisui"])
    info = {i.filename: i.compress_type for i in z.infolist()}
    assert info[pdf_name] == zipfile.ZIP_STORED
    assert info["attachments/p1.png"] == zipfile.ZIP_STORED
    assert info["00_index.md"] == zipfile.ZIP_DEFLATED
    assert info["theta-book.json"] == zipfile.ZIP_DEFLATED
    assert info["mimetype"] == zipfile.ZIP_STORED


def test_pack_missing_book_raises(tmp_path):
    cfg = PipelineConfig()
    cfg.out_dir = tmp_path / "out"
    cfg.work_dir = tmp_path / "work"
    try:
        pack.pack_book(cfg, "ない本")
        assert False, "should raise"
    except FileNotFoundError:
        pass
