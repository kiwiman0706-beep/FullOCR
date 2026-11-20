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


# --- ページアンカー: 印刷ページ番号（print=）の検出（実本で 178 中 0 → 139 ページ） ---

def test_low_confidence_margin_nombre_is_kept_as_footer():
    # NDL はノンブルの信頼度を 0.0〜0.3 と極端に低く出す → 足切りで消えていた。
    from fullocr.pipeline.merge import merge_page
    from fullocr.pipeline.ndl import NDLLine, NDLPage

    cfg = PipelineConfig()
    ndl = NDLPage(width=1000, height=1600, lines=[
        NDLLine(text="本文の0行目です。これは十分な長さの本文の行です。", bbox=BBox(x0=100, y0=300, x1=900, y1=330), confidence=0.95),
        NDLLine(text="本文の1行目です。これは十分な長さの本文の行です。", bbox=BBox(x0=100, y0=340, x1=900, y1=370), confidence=0.95),
        NDLLine(text="本文の2行目です。これは十分な長さの本文の行です。", bbox=BBox(x0=100, y0=380, x1=900, y1=410), confidence=0.95),
        NDLLine(text="本文の3行目です。これは十分な長さの本文の行です。", bbox=BBox(x0=100, y0=420, x1=900, y1=450), confidence=0.95),
        NDLLine(text="本文の4行目です。これは十分な長さの本文の行です。", bbox=BBox(x0=100, y0=460, x1=900, y1=490), confidence=0.95),
        NDLLine(text="12", bbox=BBox(x0=480, y0=1544, x1=520, y1=1561), confidence=0.0),
    ])
    marker = LayoutPage(width=1000, height=1600, blocks=[
        LayoutBlock(bbox=BBox(x0=90, y0=290, x1=910, y1=500), type=BlockType.BODY, reading_order=1),
    ])
    page = merge_page(7, ndl, marker, cfg)
    footers = [b for b in page.blocks if b.type is BlockType.PAGE_FOOTER]
    assert [b.text for b in footers] == ["12"]


def test_engine_footer_text_kept_when_ndl_missed_it():
    # NDL がノンブルを読まなかったページでも、Docling の page_footer テキストで残す。
    from fullocr.pipeline.merge import merge_page
    from fullocr.pipeline.ndl import NDLLine, NDLPage

    cfg = PipelineConfig()
    ndl = NDLPage(width=1000, height=1600, lines=[
        NDLLine(text="本文の0行目です。これは十分な長さの本文の行です。", bbox=BBox(x0=100, y0=300, x1=900, y1=330), confidence=0.95),
        NDLLine(text="本文の1行目です。これは十分な長さの本文の行です。", bbox=BBox(x0=100, y0=340, x1=900, y1=370), confidence=0.95),
        NDLLine(text="本文の2行目です。これは十分な長さの本文の行です。", bbox=BBox(x0=100, y0=380, x1=900, y1=410), confidence=0.95),
        NDLLine(text="本文の3行目です。これは十分な長さの本文の行です。", bbox=BBox(x0=100, y0=420, x1=900, y1=450), confidence=0.95),
        NDLLine(text="本文の4行目です。これは十分な長さの本文の行です。", bbox=BBox(x0=100, y0=460, x1=900, y1=490), confidence=0.95),
    ])
    marker = LayoutPage(width=1000, height=1600, blocks=[
        LayoutBlock(bbox=BBox(x0=90, y0=290, x1=910, y1=500), type=BlockType.BODY, reading_order=1),
        LayoutBlock(bbox=BBox(x0=470, y0=1540, x1=530, y1=1565), type=BlockType.PAGE_FOOTER,
                    reading_order=2, text="5 5"),
        LayoutBlock(bbox=BBox(x0=0, y0=0, x1=10, y1=10), type=BlockType.BODY, reading_order=3,
                    text="装飾"),
    ])
    page = merge_page(7, ndl, marker, cfg)
    assert [(b.type, b.text) for b in page.blocks if b.type is not BlockType.BODY] == [
        (BlockType.PAGE_FOOTER, "5 5")]
    assert all(b.text != "装飾" for b in page.blocks)  # 空の本文ブロックは従来どおり捨てる


def test_detect_nombre_variants():
    from fullocr.render.markdown import _detect_nombre

    def page(*texts, kind=BlockType.PAGE_FOOTER):
        return Page(number=10, width=100, height=100, blocks=[
            Block(id=f"b{i}", bbox=BBox(x0=0, y0=90, x1=10, y1=99), type=kind, text=t,
                  reading_order=i) for i, t in enumerate(texts)])

    assert _detect_nombre(page("5 5")) == "5"            # Docling の重複
    assert _detect_nombre(page("oo8")) == "8"            # 0 を o と誤読
    assert _detect_nombre(page("O09")) == "9"
    assert _detect_nombre(page("Ⅵ", kind=BlockType.PAGE_HEADER)) == "vi"   # 全角ローマ数字
    assert _detect_nombre(page("Ⅵ Ⅵ", kind=BlockType.PAGE_HEADER)) == "vi"
    # 柱「…乱雑さ I」の I はノンブルではない。同じページの算用数字を優先。
    assert _detect_nombre(page("第0章 エネルギーと乱雑さ I", kind=BlockType.PAGE_HEADER)) is None
    assert _detect_nombre(Page(number=3, width=1, height=1, blocks=[
        Block(id="h", bbox=BBox(x0=0, y0=0, x1=1, y1=1), type=BlockType.PAGE_HEADER,
              text="第0章 エネルギーと乱雑さ I", reading_order=0),
        Block(id="h2", bbox=BBox(x0=0, y0=0, x1=1, y1=1), type=BlockType.PAGE_HEADER,
              text="2", reading_order=1)])) == "2"
    assert _detect_nombre(page("Moore")) is None        # 英単語は拾わない


# --- プリセット ---

def test_presets_apply_and_default_anchors_on():
    from fullocr.presets import PRESETS, apply_preset

    base = PipelineConfig()
    assert base.render.page_anchors is True  # BookHatch 連携のため既定 ON
    ob = apply_preset(base, "obsidian")
    assert ob.render.pdf_page_links and ob.render.hide_toc_pages and ob.render.page_anchors
    assert base.render.pdf_page_links is False  # 元の cfg は変更しない
    # 設定ファイルで変えた無関係な項目はプリセットで消えない
    base.ndl.min_confidence = 0.7
    assert apply_preset(base, "bookhatch").ndl.min_confidence == 0.7
    assert set(PRESETS) >= {"bookhatch", "obsidian"}
    import pytest
    with pytest.raises(KeyError):
        apply_preset(base, "nope")


def test_docling_cli_gets_accuracy_flags():
    # 以前は CLI 経路で table_mode 等が無視され、A/B しても出力が同一だった。
    from fullocr.config import DoclingConfig
    from fullocr.pipeline.docling import _cli_accuracy_flags

    base = "docling convert a.pdf --to json --output o --no-ocr"
    assert _cli_accuracy_flags(base, DoclingConfig()) == " --table-mode fast"
    cfg = DoclingConfig(table_mode="accurate", enrich_formula=True)
    assert _cli_accuracy_flags(base, cfg) == " --table-mode accurate --enrich-formula"
    assert _cli_accuracy_flags(base, DoclingConfig(do_table_structure=False)).endswith("--no-tables")
    # command に明示済みならそちらを尊重して重ねない
    assert _cli_accuracy_flags(base + " --table-mode accurate", DoclingConfig()) == ""


def test_latex_formula_kept_and_ocr_formula_falls_back_to_body():
    from fullocr.pipeline.merge import merge_page
    from fullocr.render.markdown import _block_to_md

    cfg = PipelineConfig()
    lines = [NDLLine(text=f"本文の{i}行目です。これは十分な長さの本文の行です。",
                     bbox=BBox(x0=100, y0=300 + i * 40, x1=900, y1=330 + i * 40), confidence=0.95)
             for i in range(5)]
    lines.append(NDLLine(text="2H2+02~→ 2H20", bbox=BBox(x0=300, y0=600, x1=700, y1=630),
                         confidence=0.9))
    lines.append(NDLLine(text="σ め ④", bbox=BBox(x0=300, y0=700, x1=700, y1=730), confidence=0.9))
    ndl = NDLPage(width=1000, height=1600, lines=lines)
    marker = LayoutPage(width=1000, height=1600, blocks=[
        LayoutBlock(bbox=BBox(x0=90, y0=290, x1=910, y1=500), type=BlockType.BODY, reading_order=1),
        LayoutBlock(bbox=BBox(x0=290, y0=590, x1=710, y1=640), type=BlockType.FORMULA,
                    reading_order=2, text=r"2 \, H _ { 2 } + O _ { 2 } \longrightarrow 2 \, H _ { 2 } O"),
        LayoutBlock(bbox=BBox(x0=290, y0=690, x1=710, y1=740), type=BlockType.FORMULA,
                    reading_order=3, text="σ め ④"),
    ])
    page = merge_page(7, ndl, marker, cfg)
    f = [b for b in page.blocks if b.type is BlockType.FORMULA]
    assert len(f) == 1 and f[0].text.startswith(r"2 \, H")
    assert _block_to_md(f[0], RenderConfig()) == "$$\n" + f[0].text + "\n$$"
    assert any(b.type is BlockType.BODY and "σ め ④" in b.text for b in page.blocks)


def test_figure_keeps_inner_text_and_tiny_icons_are_dropped():
    # 実本「救急当直」: 章題バナー（02 発熱 / 帰すと死んじゃう…）が丸ごと図になり章題が消えていた。
    # ✕印・チェック枠など文字の無い小アイコンも図として切り出されていた。
    from fullocr.pipeline.merge import merge_page
    from fullocr.render.markdown import _block_to_md

    cfg = PipelineConfig()
    lines = [NDLLine(text=f"本文の{i}行目です。これは十分な長さの本文の行です。",
                     bbox=BBox(x0=100, y0=900 + i * 40, x1=900, y1=930 + i * 40), confidence=0.95)
             for i in range(5)]
    lines += [NDLLine(text="帰すと死んじゃう発熱を見逃すな!", bbox=BBox(x0=300, y0=100, x1=800, y1=140),
                      confidence=0.9),
              NDLLine(text="発熱", bbox=BBox(x0=300, y0=160, x1=420, y1=220), confidence=0.9)]
    ndl = NDLPage(width=1000, height=1600, lines=lines)
    marker = LayoutPage(width=1000, height=1600, blocks=[
        LayoutBlock(bbox=BBox(x0=50, y0=50, x1=950, y1=400), type=BlockType.FIGURE, reading_order=1),
        LayoutBlock(bbox=BBox(x0=90, y0=890, x1=910, y1=1100), type=BlockType.BODY, reading_order=2),
        LayoutBlock(bbox=BBox(x0=100, y0=500, x1=140, y1=540), type=BlockType.FIGURE, reading_order=3),
    ])
    page = merge_page(20, ndl, marker, cfg)
    figs = [b for b in page.blocks if b.type is BlockType.FIGURE]
    assert len(figs) == 1  # 40x40 の✕アイコン(面積0.1%)は捨てる
    assert "発熱" in figs[0].ocr_text and "見逃すな" in figs[0].ocr_text
    figs[0].image_path = "attachments/p20.png"
    md = _block_to_md(figs[0], RenderConfig())
    assert md.endswith("> [!info]- 図中のテキスト\n> 帰すと死んじゃう発熱を見逃すな!\n> 発熱")


def test_figure_text_skips_number_badges():
    from fullocr.pipeline.merge import _figure_text

    ln = lambda t: NDLLine(text=t, bbox=BBox(x0=0, y0=0, x1=10, y1=10), confidence=0.9)
    assert _figure_text([ln("5")]) == ""
    assert _figure_text([ln("1.")]) == ""
    assert _figure_text([ln("30"), ln("→")]) == ""
    assert _figure_text([ln("発熱")]) == "発熱"
    assert _figure_text([ln("aVR")]) == "aVR"
