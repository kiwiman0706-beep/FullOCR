"""born-digital 判定とテキスト層抽出（pdftext）の検証。実PDFを fitz で合成する。"""

from __future__ import annotations

import pytest

from fullocr.pipeline import pdftext

fitz = pytest.importorskip("fitz")


def _born_digital_pdf(path, pages=4):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=400, height=560)
        body = (f"第{i + 1}章 本文のテスト。" + "これは電子生成PDFの段落です。" * 20)
        page.insert_textbox(fitz.Rect(40, 40, 360, 540), body, fontsize=10, fontname="japan")
    doc.save(str(path))
    doc.close()


def _scanned_pdf(path, pages=4):
    # 全面画像＋薄いテキスト層（ScanSnap を模す）。
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 560))
    pix.clear_with(240)
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=400, height=560)
        page.insert_image(page.rect, pixmap=pix)        # ページ全面の画像
        page.insert_text((45, 60), "少し", fontsize=9, fontname="japan")
    doc.save(str(path))
    doc.close()


def test_born_digital_detected(tmp_path):
    p = tmp_path / "digital.pdf"
    _born_digital_pdf(p)
    assert pdftext.is_born_digital(p, min_chars=50) is True


def test_scanned_not_born_digital(tmp_path):
    p = tmp_path / "scan.pdf"
    _scanned_pdf(p)
    # 全面画像があるので born-digital 扱いしない（文字も乏しい）。
    assert pdftext.is_born_digital(p, min_chars=50) is False


def test_extract_text_layer_pages(tmp_path):
    p = tmp_path / "digital.pdf"
    _born_digital_pdf(p, pages=3)
    pages = pdftext.load_text_layer_pages(p)
    assert len(pages) == 3
    assert all(pg.width == 400 and pg.height == 560 for pg in pages)
    assert any("本文" in ln.text for pg in pages for ln in pg.lines)
    # 全行に bbox と confidence(=1.0) がある
    assert all(ln.confidence == 1.0 for pg in pages for ln in pg.lines)


def test_cache_roundtrip(tmp_path):
    p = tmp_path / "digital.pdf"
    _born_digital_pdf(p, pages=2)
    pages = pdftext.load_text_layer_pages(p)
    cache = tmp_path / "textlayer.json"
    pdftext.save_pages(pages, cache)
    back = pdftext.load_pages_json(cache)
    assert len(back) == len(pages)
    assert back[0].lines[0].text == pages[0].lines[0].text
    assert back[0].lines[0].bbox.x0 == pages[0].lines[0].bbox.x0


def test_build_ir_uses_text_layer_and_skips_ndl(tmp_path, monkeypatch):
    """born-digital PDF は NDL を呼ばずにテキスト層から IR を作る。"""
    from fullocr import batch
    from fullocr.config import PipelineConfig
    from fullocr.pipeline import ndl as ndl_mod

    pdf = tmp_path / "digital.pdf"
    _born_digital_pdf(pdf, pages=3)
    cfg = PipelineConfig()
    cfg.work_dir = tmp_path / "work"
    cfg.engine = "docling"
    cfg.docling.enabled = False   # レイアウトエンジンは呼ばない（テキスト層のみ）

    def boom(*a, **k):
        raise AssertionError("born-digital では NDL を呼ばない")

    monkeypatch.setattr(ndl_mod, "run_ndl", boom)

    doc = batch.build_ir_for_pdf(pdf, cfg, run_engines=True)
    assert len(doc.pages) == 3
    assert any("本文" in b.text for p in doc.pages for b in p.blocks)
    # キャッシュが残り、--no-engines で再構築できる
    assert (cfg.work_dir / "digital" / "textlayer.json").exists()
    doc2 = batch.build_ir_for_pdf(pdf, cfg, run_engines=False)
    assert len(doc2.pages) == 3
