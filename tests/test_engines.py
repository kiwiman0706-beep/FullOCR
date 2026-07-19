"""Docling / MinerU アダプタの合成データ検証。

各エンジンの実バイナリが無くても、出力JSONの想定スキーマに対して
パース→共通LayoutPage→NDLとのマージが成立することを確認する。
どのエンジンでも同じ IR/Markdown 生成に載ることを担保する。
"""

from __future__ import annotations

from fullocr.config import PipelineConfig
from fullocr.ir import BlockType
from fullocr.pipeline.docling import parse_docling_json
from fullocr.pipeline.merge import build_document
from fullocr.pipeline.mineru import parse_mineru_json
from fullocr.pipeline.ndl import parse_ndl_json


def _ndl_fixture() -> dict:
    """1ページ: 見出し行 + 本文 + 図キャプション（画像px座標 1000x1400）。"""
    return {
        "pages": [
            {
                "width": 1000,
                "height": 1400,
                "contents": [
                    {"text": "第一章 導入",
                     "boundingBox": [[100, 80], [500, 80], [500, 130], [100, 130]]},
                    {"text": "これは本文です。",
                     "boundingBox": [[100, 200], [700, 200], [700, 240], [100, 240]]},
                    {"text": "図1 実験装置",
                     "boundingBox": [[100, 1100], [400, 1100], [400, 1140], [100, 1140]]},
                ],
            }
        ]
    }


def _docling_fixture() -> dict:
    """DoclingDocument JSON（body の読み順・BOTTOMLEFT座標・section_header level）。"""
    return {
        "pages": {"1": {"size": {"width": 500, "height": 700}, "page_no": 1}},
        "body": {
            "children": [
                {"$ref": "#/texts/0"},
                {"$ref": "#/texts/1"},
                {"$ref": "#/pictures/0"},
                {"$ref": "#/texts/2"},
            ]
        },
        "texts": [
            # 座標は BOTTOMLEFT（下原点）。ページ高700。見出しは上部＝t,bが大きい。
            {"self_ref": "#/texts/0", "label": "section_header", "level": 1,
             "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 660, "r": 250, "b": 635,
                                              "coord_origin": "BOTTOMLEFT"}}], "text": "見出し"},
            {"self_ref": "#/texts/1", "label": "text",
             "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 600, "r": 350, "b": 555,
                                              "coord_origin": "BOTTOMLEFT"}}], "text": "本文"},
            {"self_ref": "#/texts/2", "label": "caption",
             "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 150, "r": 200, "b": 130,
                                              "coord_origin": "BOTTOMLEFT"}}], "text": "キャプション"},
        ],
        "pictures": [
            {"self_ref": "#/pictures/0", "label": "picture",
             "prov": [{"page_no": 1, "bbox": {"l": 50, "t": 300, "r": 450, "b": 160,
                                              "coord_origin": "BOTTOMLEFT"}}]},
        ],
        "tables": [],
        "groups": [],
    }


def _mineru_fixture() -> dict:
    """MinerU middle.json（pdf_info/preproc_blocks、image は入れ子blocks）。"""
    return {
        "pdf_info": [
            {
                "page_idx": 0,
                "page_size": [500, 700],
                "preproc_blocks": [
                    {"type": "title", "bbox": [50, 40, 250, 65], "text_level": 1,
                     "lines": [{"spans": [{"content": "見出し"}]}]},
                    {"type": "text", "bbox": [50, 100, 350, 145],
                     "lines": [{"spans": [{"content": "本文"}]}]},
                    {"type": "image", "bbox": [50, 400, 450, 560], "blocks": [
                        {"type": "image_body", "bbox": [50, 400, 450, 540]},
                        {"type": "image_caption", "bbox": [50, 545, 200, 560],
                         "lines": [{"spans": [{"content": "キャプション"}]}]},
                    ]},
                ],
            }
        ]
    }


# --- Docling ---------------------------------------------------------------

def test_docling_parse_types_and_order():
    pages = parse_docling_json(_docling_fixture())
    assert len(pages) == 1
    p = pages[0]
    assert p.width == 500 and p.height == 700
    types = [b.type for b in p.blocks]
    assert types[0] is BlockType.HEADING
    assert BlockType.FIGURE in types
    assert BlockType.CAPTION in types
    # 読み順は body ツリー順
    assert [b.reading_order for b in p.blocks] == [1, 2, 3, 4]
    # section_header level が見出しレベルへ
    assert p.blocks[0].heading_level == 1


def test_docling_bottomleft_coords_flipped():
    """BOTTOMLEFT 座標が左上原点に正しく反転される（見出しがページ上部にくる）。"""
    p = parse_docling_json(_docling_fixture())[0]
    heading = p.blocks[0]
    figure = next(b for b in p.blocks if b.type is BlockType.FIGURE)
    # 見出し(上部)の y0 は 図(中央)の y0 より小さい
    assert heading.bbox.y0 < figure.bbox.y0


def test_docling_merges_with_ndl():
    cfg = PipelineConfig()
    ndl = parse_ndl_json(_ndl_fixture())  # 1000x1400 px
    layout = parse_docling_json(_docling_fixture())  # 500x700
    doc = build_document(ndl, layout, cfg)
    page = doc.pages[0]
    by_type = {b.type: b for b in page.blocks}
    # 正規化マージで NDL の見出しテキストが Docling の見出しブロックに載る
    assert "第一章" in by_type[BlockType.HEADING].text
    assert BlockType.FIGURE in by_type


# --- MinerU ----------------------------------------------------------------

def test_mineru_parse_nested_image_blocks():
    pages = parse_mineru_json(_mineru_fixture())
    assert len(pages) == 1
    p = pages[0]
    assert p.width == 500 and p.height == 700
    types = [b.type for b in p.blocks]
    assert BlockType.HEADING in types
    assert BlockType.FIGURE in types   # image_body
    assert BlockType.CAPTION in types  # image_caption（入れ子から展開）


def test_mineru_merges_with_ndl():
    cfg = PipelineConfig()
    ndl = parse_ndl_json(_ndl_fixture())
    layout = parse_mineru_json(_mineru_fixture())
    doc = build_document(ndl, layout, cfg)
    by_type = {b.type: b for b in doc.pages[0].blocks}
    assert "第一章" in by_type[BlockType.HEADING].text
    assert BlockType.FIGURE in by_type


def test_engine_selection_default_is_docling():
    cfg = PipelineConfig()
    assert cfg.engine == "docling"
