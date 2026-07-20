"""合成データでパイプライン（パース→マージ→レンダリング）を検証。

実 NDL/Marker バイナリは無くても、アダプタのパーサとマージ・Markdown生成の
論理を end-to-end で確認できる。
"""

from __future__ import annotations

from fullocr.config import PipelineConfig, RenderConfig
from fullocr.ir import BlockType, Document
from fullocr.pipeline.marker import parse_marker_json
from fullocr.pipeline.merge import build_document
from fullocr.pipeline.ndl import parse_ndl_json
from fullocr.render import render_document
from fullocr.render.toc import build_toc


# --- 合成フィクスチャ -------------------------------------------------------

def _ndl_fixture() -> list:
    """1ページ: 見出し行 + 本文2行 + 図キャプション。座標は画像px想定。"""
    return {
        "pages": [
            {
                "width": 1000,
                "height": 1400,
                "contents": [
                    {"text": "第一章 導入", "boundingBox": [[100, 80], [500, 80], [500, 130], [100, 130]], "confidence": 0.99, "direction": "horizontal"},
                    {"text": "これは本文の一行目です。", "boundingBox": [[100, 200], [700, 200], [700, 240], [100, 240]], "confidence": 0.98},
                    {"text": "これは本文の二行目です。", "boundingBox": [[100, 250], [700, 250], [700, 290], [100, 290]], "confidence": 0.97},
                    {"text": "図1 実験装置", "boundingBox": [[100, 1100], [400, 1100], [400, 1140], [100, 1140]], "confidence": 0.95},
                ],
            }
        ]
    }


def _marker_fixture() -> list:
    """同じページのレイアウト。Marker 座標系はスケール違い(幅500,高700)にしてある。"""
    return {
        "block_type": "Document",
        "children": [
            {
                "block_type": "Page",
                "bbox": [0, 0, 500, 700],
                "children": [
                    {"block_type": "SectionHeader", "bbox": [50, 40, 250, 65], "html": "<h1>x</h1>"},
                    {"block_type": "Text", "bbox": [50, 100, 350, 145]},
                    {"block_type": "Figure", "bbox": [50, 400, 450, 540]},
                    {"block_type": "Caption", "bbox": [50, 550, 200, 570]},
                ],
            }
        ],
    }


def test_ndl_parser_reads_lines():
    pages = parse_ndl_json(_ndl_fixture())
    assert len(pages) == 1
    assert pages[0].width == 1000
    assert len(pages[0].lines) == 4
    assert pages[0].lines[0].text == "第一章 導入"


def test_marker_parser_maps_types():
    pages = parse_marker_json(_marker_fixture())
    assert len(pages) == 1
    types = [b.type for b in pages[0].blocks]
    assert BlockType.HEADING in types
    assert BlockType.FIGURE in types
    assert BlockType.CAPTION in types
    # 読み順が付与されている
    assert [b.reading_order for b in pages[0].blocks] == [1, 2, 3, 4]


def test_merge_assigns_ndl_text_to_marker_blocks():
    cfg = PipelineConfig()
    ndl = parse_ndl_json(_ndl_fixture())
    marker = parse_marker_json(_marker_fixture())
    doc = build_document(ndl, marker, cfg)

    assert len(doc.pages) == 1
    page = doc.pages[0]
    by_type = {b.type: b for b in page.blocks}

    # 見出しに NDL テキストが載っている
    assert by_type[BlockType.HEADING].text == "第一章 導入"
    assert by_type[BlockType.HEADING].heading_level == 1
    # 本文2行が結合されている
    assert "一行目" in by_type[BlockType.BODY].text
    assert "二行目" in by_type[BlockType.BODY].text
    # 図ブロックが領域として残る
    assert BlockType.FIGURE in by_type
    # キャプションに図キャプションのテキスト
    assert "図1" in by_type[BlockType.CAPTION].text


def test_merge_without_marker_falls_back_to_ndl():
    cfg = PipelineConfig()
    ndl = parse_ndl_json(_ndl_fixture())
    doc = build_document(ndl, [], cfg)
    page = doc.pages[0]
    # Marker 無しでも全テキストが残る（縮退動作）
    joined = "".join(b.text for b in page.blocks)
    assert "第一章" in joined
    assert "一行目" in joined


def test_toc_built_from_headings():
    cfg = PipelineConfig()
    doc = build_document(parse_ndl_json(_ndl_fixture()), parse_marker_json(_marker_fixture()), cfg)
    toc = build_toc(doc)
    assert any(e.text == "第一章 導入" for e in toc)


def test_toc_links_cross_file_to_body(tmp_path):
    """目次リンクが index 内アンカーではなく本文ファイル#見出しを指す。"""
    cfg = PipelineConfig()
    doc = build_document(parse_ndl_json(_ndl_fixture()), parse_marker_json(_marker_fixture()), cfg)
    doc.meta.title = "テスト本"
    render_document(doc, tmp_path, RenderConfig(split_by_heading_level=None))
    index = (tmp_path / "00_index.md").read_text(encoding="utf-8")
    # 同一ファイルアンカー [[#...]] ではなく [[本文#...]] になっている
    assert "[[#第一章 導入]]" not in index
    assert "[[テスト本#第一章 導入]]" in index
    # リンク先の本文ファイルが実在する
    assert (tmp_path / "テスト本.md").exists()


def test_toc_bracket_heading_degrades_safely():
    """[] を含む見出しは壊れた wikilink を出さず、ファイル先頭リンクへ劣化する。"""
    from fullocr.render.toc import TocEntry, toc_to_markdown

    entry = TocEntry(level=1, text="脳血管障害[CVD]", page=1, slug="x", target_file="本文")
    md = toc_to_markdown([entry])
    # アンカーを含む壊れたリンクにしない（'#' を出さない）
    assert "#" not in md
    # ファイルへのリンクにはなっている
    assert "[[本文" in md


def test_render_writes_markdown(tmp_path):
    cfg = PipelineConfig()
    doc = build_document(parse_ndl_json(_ndl_fixture()), parse_marker_json(_marker_fixture()), cfg)
    written = render_document(doc, tmp_path, RenderConfig())
    names = {p.name for p in written}
    assert "00_index.md" in names

    index = (tmp_path / "00_index.md").read_text(encoding="utf-8")
    assert "目次" in index
    assert "第一章 導入" in index

    # 本文ファイルに見出しと本文が入っている
    body_files = [p for p in written if p.name != "00_index.md"]
    all_body = "\n".join(p.read_text(encoding="utf-8") for p in body_files)
    assert "# 第一章 導入" in all_body
    assert "一行目" in all_body


def test_ir_json_roundtrip(tmp_path):
    cfg = PipelineConfig()
    doc = build_document(parse_ndl_json(_ndl_fixture()), parse_marker_json(_marker_fixture()), cfg)
    p = tmp_path / "ir.json"
    doc.save(p)
    doc2 = Document.load(p)
    assert doc2.pages[0].blocks[0].text == doc.pages[0].blocks[0].text


def test_vertical_line_ordering():
    """縦書き行はブロック内で右→左に並ぶ。"""
    data = {
        "pages": [
            {
                "width": 1000,
                "height": 1000,
                "contents": [
                    {"text": "左", "boundingBox": [[100, 100], [140, 100], [140, 300], [100, 300]], "direction": "vertical"},
                    {"text": "右", "boundingBox": [[800, 100], [840, 100], [840, 300], [800, 300]], "direction": "vertical"},
                ],
            }
        ]
    }
    cfg = PipelineConfig()
    doc = build_document(parse_ndl_json(data), [], cfg)
    joined = "".join(b.text for b in doc.pages[0].ordered_blocks())
    assert joined == "右左"  # 右が先
