"""印刷目次検出・章連番チェック・目次非表示レンダリングの検証。"""

from __future__ import annotations

from fullocr.config import RenderConfig
from fullocr.ir import BBox, Block, BlockType, DocMeta, Document, Page
from fullocr.pipeline import tocdetect
from fullocr.render.markdown import _block_to_md, _split_documents


def _page(number: int, lines: list[tuple[str, BlockType]]) -> Page:
    p = Page(number=number, width=1000, height=1400)
    for i, (text, typ) in enumerate(lines):
        p.blocks.append(Block(id=f"p{number}b{i}", bbox=BBox(x0=0, y0=i * 30, x1=500, y1=i * 30 + 25),
                              type=typ, text=text, reading_order=i))
    return p


def _toc_page(number: int) -> Page:
    return _page(number, [
        ("目次", BlockType.HEADING),
        ("第1章 総論 …………… 1", BlockType.BODY),
        ("第2章 各論 …………… 25", BlockType.BODY),
        ("第3章 応用 …………… 80", BlockType.BODY),
        ("索引 …………… 120", BlockType.BODY),
    ])


def test_detect_toc_page_at_front():
    doc = Document(pages=[
        _toc_page(1),
        _page(2, [("本文がここから始まる。", BlockType.BODY)]),
        _page(3, [("さらに本文。", BlockType.BODY)]),
        _page(4, [("まだ本文。", BlockType.BODY)]),
        _page(5, [("おわり。", BlockType.BODY)]),
    ])
    assert tocdetect.detect_toc_pages(doc) == [1]


def test_plain_pages_are_not_toc():
    doc = Document(pages=[_page(i, [("ふつうの文章。", BlockType.BODY)]) for i in range(1, 6)])
    assert tocdetect.detect_toc_pages(doc) == []


def test_heading_sequence_flags_gap_and_dup():
    doc = Document(pages=[
        _page(1, [("第1章 はじめ", BlockType.HEADING)]),
        _page(2, [("第3章 とばし", BlockType.HEADING)]),   # 第2章 が飛んでいる
        _page(3, [("第3章 だぶり", BlockType.HEADING)]),   # 第3章 重複
    ])
    report = tocdetect.heading_sequence_report(doc)
    assert report is not None
    assert "飛び" in report and "第2章" in report
    assert "重複" in report


def test_heading_sequence_ok_returns_none():
    doc = Document(pages=[
        _page(1, [("第1章 A", BlockType.HEADING)]),
        _page(2, [("第2章 B", BlockType.HEADING)]),
    ])
    assert tocdetect.heading_sequence_report(doc) is None


def test_table_rendered_as_image_with_caption():
    b = Block(id="t1", bbox=BBox(x0=0, y0=0, x1=100, y1=100), type=BlockType.TABLE,
              text="行1 | 行2", image_path="attachments/t1.png")
    # 既定(tables_as_images=True): 画像＋検索用キャプション
    md = _block_to_md(b, RenderConfig())
    assert "attachments/t1.png" in md
    assert "行1 | 行2" in md
    # off にすると従来のテキスト
    md2 = _block_to_md(b, RenderConfig(tables_as_images=False))
    assert "attachments/t1.png" not in md2
    assert "行1 | 行2" in md2


def test_pdf_page_links_on_headings():
    doc = Document(meta=DocMeta(title="t", pdf_name="t_text.pdf"), pages=[
        _page(5, [("第2章 各論", BlockType.HEADING), ("本文。", BlockType.BODY)]),
    ])
    cfg = RenderConfig(pdf_page_links=True, front_matter=False)
    body = "\n\n".join(_split_documents(doc, cfg)[0][1])
    # 見出し行に PDF の該当ページ(5)へのリンクが付く
    assert "[[t_text.pdf#page=5|📄原文 p.5]]" in body
    # 本文には付かない
    assert "本文。" in body


def test_pdf_page_links_off_by_default():
    doc = Document(meta=DocMeta(title="t", pdf_name="t_text.pdf"), pages=[
        _page(1, [("第1章", BlockType.HEADING)]),
    ])
    body = "\n\n".join(_split_documents(doc, RenderConfig(front_matter=False))[0][1])
    assert "#page=" not in body


def test_hide_toc_pages_comments_out_content():
    doc = Document(meta=DocMeta(title="t", toc_pages=[1]), pages=[
        _toc_page(1),
        _page(2, [("本文。", BlockType.BODY)]),
    ])
    cfg = RenderConfig(hide_toc_pages=True, front_matter=False)
    (_name, body) = _split_documents(doc, cfg)[0]
    joined = "\n\n".join(body)
    # 目次ページの行は %% で囲まれて隠れている
    assert "%%" in joined
    assert "第2章 各論" in joined  # 目次エントリ（コメント内）
    # 本文ページはコメントされない
    assert "本文。" in joined
    # 目次の「第2章 各論 …」はコメント内なので、%% と同じ塊にある
    idx = joined.index("第2章 各論")
    assert "%%" in joined[:idx]
