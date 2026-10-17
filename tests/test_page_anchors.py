"""ページアンカー（MDの位置→原PDFページ の不可視マーカー）の検証。

dsbooks 等の外部リーダー向け仕様 docs/page-anchor-spec.md の生成側テスト。
- アウトライン(# 見出し)を汚さない（HTMLコメントなので見出しに混ざらない）
- pdf= は物理ページ、print= は柱/ノンブルから拾った印刷ページ
- front-matter に scheme / pdf_file / page_offset を宣言
"""

from __future__ import annotations

import re

from fullocr.config import RenderConfig
from fullocr.ir import BBox, Block, BlockType, DocMeta, Document, Page
from fullocr.render import markdown as md


def _page(number, printed=None, heading=None):
    blocks = []
    if heading:
        blocks.append(Block(id=f"h{number}", bbox=BBox(x0=0, y0=0, x1=400, y1=40),
                            type=BlockType.HEADING, text=heading, heading_level=1, reading_order=0))
    blocks.append(Block(id=f"b{number}", bbox=BBox(x0=0, y0=50, x1=400, y1=90),
                        type=BlockType.BODY, text=f"本文{number}", reading_order=1))
    if printed is not None:
        blocks.append(Block(id=f"f{number}", bbox=BBox(x0=180, y0=1360, x1=220, y1=1390),
                            type=BlockType.PAGE_FOOTER, text=printed, reading_order=9))
    return Page(number=number, width=1000, height=1400, blocks=blocks)


def _render_single(doc, cfg, tmp_path):
    out = tmp_path / "book"
    md.render_document(doc, out, cfg)
    text = (out / "00_index.md").read_text(encoding="utf-8")
    body = next(p for p in out.glob("*.md") if p.name != "00_index.md").read_text(encoding="utf-8")
    return text, body


def test_anchor_is_invisible_and_carries_pdf_and_print(tmp_path):
    doc = Document(meta=DocMeta(title="t", pdf_name="book_text.pdf"),
                   pages=[_page(11, printed="３"), _page(12, printed="４")])
    cfg = RenderConfig(page_anchors=True)
    index, body = _render_single(doc, cfg, tmp_path)

    anchors = re.findall(r"<!-- fullocr:page pdf=(\d+)(?: print=\"([^\"]+)\")? -->", body)
    assert anchors == [("11", "3"), ("12", "4")]  # 物理ページ11/12・印刷3/4(全角正規化)
    # 見出しではないので # 行には出ない → アウトラインを汚さない
    assert not re.search(r"^#.*fullocr:page", body, re.MULTILINE)
    # front-matter 宣言
    assert f"page_anchors: {md.PAGE_ANCHOR_SCHEME}" in index
    assert "pdf_file: book_text.pdf" in index
    # 2ページだけなので offset(<3ページ一致) は確信不足 → 宣言しない
    assert "page_offset:" not in index


def test_offset_declared_only_when_confident(tmp_path):
    # 4ページ分で offset=8 が一致 → front-matter に出る
    pages = [_page(9, printed="1"), _page(10, printed="2"),
             _page(11, printed="3"), _page(12, printed="4")]
    doc = Document(meta=DocMeta(title="t", pdf_name="b.pdf"), pages=pages)
    index, _ = _render_single(doc, RenderConfig(page_anchors=True), tmp_path)
    assert "page_offset: 8" in index


def test_no_anchor_when_disabled(tmp_path):
    doc = Document(meta=DocMeta(title="t"), pages=[_page(1, printed="1")])
    _, body = _render_single(doc, RenderConfig(page_anchors=False), tmp_path)
    assert "fullocr:page" not in body


def test_anchor_in_split_lands_with_first_block(tmp_path):
    # 章頭見出しのあるページ: アンカーはその章ファイル側に入る（前章に取り残されない）
    doc = Document(meta=DocMeta(title="t", pdf_name="b.pdf"), pages=[
        _page(1, printed="1"),
        _page(2, printed="2", heading="第2章"),
    ])
    out = tmp_path / "book"
    md.render_document(doc, out, RenderConfig(page_anchors=True, split_by_heading_level=1))
    ch2 = [p for p in out.glob("*.md") if "第2章" in p.name or "02" in p.name]
    assert ch2, "第2章のファイルが生成される"
    txt = ch2[0].read_text(encoding="utf-8")
    assert "pdf=2" in txt and "# 第2章" in txt


def test_roman_numeral_front_matter_kept_as_print(tmp_path):
    doc = Document(meta=DocMeta(title="t", pdf_name="b.pdf"),
                   pages=[_page(3, printed="ⅳ") if False else _page(3, printed="iv")])
    _, body = _render_single(doc, RenderConfig(page_anchors=True), tmp_path)
    assert 'print="iv"' in body
