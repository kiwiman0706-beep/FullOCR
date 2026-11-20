"""実本サンプル検証（CKD診療ガイド / 3秒で心電図を読む本 / 寝る整体）で見つかった
不具合の再発防止テスト。いずれも合成データで、実バイナリ不要。"""

from __future__ import annotations

from fullocr.config import PipelineConfig, RenderConfig
from fullocr.ir import BBox, Block, BlockType, Document, Orientation, Page
from fullocr.pipeline.layout import LayoutBlock, LayoutPage
from fullocr.pipeline.merge import (
    _join_text,
    _order_lines,
    demote_false_headings,
    merge_page,
)
from fullocr.pipeline.ndl import NDLLine, NDLPage
from fullocr.pipeline.textclean import clean_document
from fullocr.render.markdown import _block_to_md


def _h(text, x0, y0, x1, y1):
    return NDLLine(text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1))


def _v(text, x0, y0, x1, y1):
    return NDLLine(text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                   orientation=Orientation.VERTICAL)


def test_side_by_side_columns_are_read_column_by_column():
    # 左段2行・右段1行（行数が違う＝本当の段組）。上→下だけで並べると交互に混ざる。
    lines = [
        _h("3.人類の健康を脅かす疾患群とし", 100, 100, 500, 130),
        _h("4.わが国のCKD対策の課題", 560, 100, 900, 130),
        _h("てのCKD", 130, 140, 260, 170),
    ]
    assert _join_text(lines) == "3.人類の健康を脅かす疾患群としてのCKD4.わが国のCKD対策の課題"


def test_table_like_rows_keep_row_order():
    # 氏名・所属のように各段の行数が揃っているものは表の行として行順のまま。
    lines = [
        _h("松尾", 100, 100, 200, 130), _h("名古屋大学", 400, 100, 600, 130),
        _h("渡辺", 100, 140, 200, 170), _h("福島医大", 400, 140, 600, 170),
    ]
    assert [ln.text for ln in _order_lines(lines)] == ["松尾", "名古屋大学", "渡辺", "福島医大"]


def test_toc_lines_keep_line_breaks():
    lines = [
        _h("はじめに……………3", 100, 100, 600, 130),
        _h("第1章 総論……………10", 100, 140, 600, 170),
    ]
    assert _join_text(lines) == "はじめに……………3\n第1章 総論……………10"


def test_vertical_page_uses_ndl_reading_order():
    # 縦書き: NDL の行順（右→左）が正。構造エンジンは左から順に返してくる。
    ndl = NDLPage(width=1000, height=1400, lines=[
        _v("一段落目です。", 850, 100, 900, 1300),
        _v("二段落目です。", 450, 100, 500, 1300),
    ])
    layout = LayoutPage(width=1000, height=1400, blocks=[
        LayoutBlock(bbox=BBox(x0=400, y0=90, x1=510, y1=1310), type=BlockType.BODY, reading_order=1),
        LayoutBlock(bbox=BBox(x0=800, y0=90, x1=910, y1=1310), type=BlockType.BODY, reading_order=2),
    ])
    page = merge_page(1, ndl, layout, PipelineConfig())
    assert [b.text for b in page.ordered_blocks()] == ["一段落目です。", "二段落目です。"]


def test_caption_moves_next_to_its_figure():
    ndl = NDLPage(width=1000, height=1400, lines=[
        _h("本文の段落です。", 100, 900, 900, 940),
        _h("図4 CKDと心血管疾患", 200, 620, 800, 650),
    ])
    layout = LayoutPage(width=1000, height=1400, blocks=[
        LayoutBlock(bbox=BBox(x0=100, y0=100, x1=900, y1=600), type=BlockType.FIGURE, reading_order=1),
        LayoutBlock(bbox=BBox(x0=90, y0=890, x1=910, y1=950), type=BlockType.BODY, reading_order=2),
        LayoutBlock(bbox=BBox(x0=190, y0=610, x1=810, y1=660), type=BlockType.CAPTION, reading_order=3),
    ])
    page = merge_page(1, ndl, layout, PipelineConfig())
    assert [b.type for b in page.ordered_blocks()] == [
        BlockType.FIGURE, BlockType.CAPTION, BlockType.BODY,
    ]


def _doc_with(*blocks: Block) -> Document:
    page = Page(number=1, width=1000, height=1400, blocks=list(blocks))
    return Document(pages=[page])


def _blk(i, t, text, **kw):
    return Block(id=f"b{i}", bbox=BBox(x0=0, y0=i, x1=10, y1=i + 1), type=t, text=text,
                 reading_order=i, **kw)


def test_sentence_like_headings_are_demoted():
    doc = _doc_with(
        _blk(1, BlockType.HEADING, "ことでしょう。", heading_level=1),
        _blk(2, BlockType.HEADING, "31", heading_level=1),
        _blk(3, BlockType.HEADING, "第1章 はじめに", heading_level=1),
    )
    assert demote_false_headings(doc) == 2
    assert [b.type for b in doc.pages[0].blocks] == [
        BlockType.BODY, BlockType.BODY, BlockType.HEADING,
    ]


def test_paragraph_split_by_figure_or_footer_is_rejoined():
    doc = _doc_with(
        _blk(1, BlockType.BODY, "骨格が常時バランスをとってくれているため。そし"),
        _blk(2, BlockType.FIGURE, ""),
        _blk(3, BlockType.PAGE_FOOTER, "12"),
        _blk(4, BlockType.BODY, "て、運動や頭の傾きを支えています。"),
    )
    clean_document(doc)
    texts = [b.text for b in doc.pages[0].ordered_blocks() if b.type is BlockType.BODY]
    assert texts == ["骨格が常時バランスをとってくれているため。そして、運動や頭の傾きを支えています。"]


def test_toc_rows_rescued_one_by_one_keep_breaks():
    doc = _doc_with(
        _blk(1, BlockType.BODY, "「心電図が好き」という人は?6"),
        _blk(2, BlockType.BODY, "心電図でしか分からないこと 8"),
        _blk(3, BlockType.BODY, "第2章 視線と視点"),
    )
    clean_document(doc)
    assert doc.pages[0].blocks[0].text == "「心電図が好き」という人は?6\n心電図でしか分からないこと 8\n第2章 視線と視点"


def test_list_bullet_glyph_not_doubled():
    b = _blk(1, BlockType.LIST, "●CKDは重要な疾患である.")
    assert _block_to_md(b, RenderConfig()) == "- CKDは重要な疾患である."


def test_table_text_goes_into_collapsed_callout():
    b = _blk(1, BlockType.TABLE, "| a | b |\n| --- | --- |\n| 1 | 2 |",
             image_path="attachments/t.png")
    md = _block_to_md(b, RenderConfig())
    assert md.startswith("![[attachments/t.png]]")
    assert "> [!info]- 表のテキスト" in md
    assert "> | 1 | 2 |" in md
    assert "*|" not in md


def test_toc_page_split_by_newlines_is_detected_and_headings_demoted():
    from fullocr.pipeline.tocdetect import demote_toc_headings, detect_toc_pages

    toc_text = "\n".join(f"第{i}章 項目{i}……{i * 10}" for i in range(1, 8))
    page = Page(number=1, width=1000, height=1400, blocks=[
        _blk(1, BlockType.HEADING, "第1章 熱化学", heading_level=1),
        _blk(2, BlockType.BODY, toc_text),
    ])
    doc = Document(pages=[page])
    doc.meta.toc_pages = detect_toc_pages(doc)
    assert doc.meta.toc_pages == [1]
    assert demote_toc_headings(doc) == 1
    assert page.blocks[0].type is BlockType.BODY


def test_docling_document_index_label_is_text():
    from fullocr.pipeline.docling import _label_to_type

    assert _label_to_type("document_index") is BlockType.BODY
    assert _label_to_type("key_value_area") is BlockType.BODY


def test_chapter_title_page_becomes_heading():
    ndl = NDLPage(width=800, height=1200, lines=[
        _h("11", 40, 240, 120, 400),  # 大きな章番号（誤読込み）
        _h("はじめに…", 45, 438, 400, 480),
        _h("心電図を3ステップで", 40, 533, 600, 580),
        _h("読んでみよう", 40, 597, 400, 640),
    ])
    layout = LayoutPage(width=400, height=600, blocks=[
        LayoutBlock(bbox=BBox(x0=15, y0=116, x1=59, y1=204), type=BlockType.HEADING, reading_order=1),
        LayoutBlock(bbox=BBox(x0=15, y0=210, x1=300, y1=320), type=BlockType.BODY, reading_order=2),
    ])
    page = merge_page(11, ndl, layout, PipelineConfig())
    assert [(b.type, b.text) for b in page.blocks] == [
        (BlockType.HEADING, "はじめに… 心電図を3ステップで 読んでみよう"),
    ]
