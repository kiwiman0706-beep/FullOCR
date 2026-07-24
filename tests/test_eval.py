"""目次評価ハーネスの検証。"""

from __future__ import annotations

from fullocr.eval import evaluate_document, printed_toc_titles
from fullocr.ir import BBox, Block, BlockType, DocMeta, Document, Page


def _page(number, lines):
    p = Page(number=number, width=1000, height=1400)
    for i, (text, typ, lvl) in enumerate(lines):
        p.blocks.append(Block(id=f"p{number}b{i}", bbox=BBox(x0=0, y0=i * 30, x1=500, y1=i * 30 + 25),
                              type=typ, text=text, heading_level=lvl, reading_order=i))
    return p


def _doc():
    # p1: 印刷目次（3項目）, p2-3: 本文＋見出し（第1章・第2章のみ＝第3章が抜け）
    return Document(meta=DocMeta(title="t", toc_pages=[1]), pages=[
        _page(1, [
            ("目次", BlockType.HEADING, 1),
            ("第1章 総論 …………… 1", BlockType.BODY, None),
            ("第2章 各論 …………… 25", BlockType.BODY, None),
            ("第3章 応用 …………… 80", BlockType.BODY, None),
        ]),
        _page(2, [("第1章 総論", BlockType.HEADING, 1), ("本文。", BlockType.BODY, None)]),
        _page(3, [("第2章 各論", BlockType.HEADING, 1)]),
    ])


def test_printed_toc_titles_strips_page_numbers():
    titles = printed_toc_titles(_doc())
    assert "第1章 総論" in titles
    assert "第3章 応用" in titles
    assert all("…" not in t for t in titles)   # リーダー除去
    assert "目次" not in titles                  # 目次見出しは除外


def test_evaluate_recall_and_missing():
    rep = evaluate_document(_doc(), book="t")
    assert rep.toc_entries == 3          # 目次項目3
    assert rep.matched == 2              # 第1章・第2章は見出しにある
    assert rep.toc_recall == round(2 / 3, 3)
    assert any("第3章" in m for m in rep.missing)   # 第3章は取りこぼし


def test_evaluate_no_toc_pages_gives_none_recall():
    doc = Document(meta=DocMeta(title="t", toc_pages=[]), pages=[
        _page(1, [("第1章", BlockType.HEADING, 1)]),
    ])
    rep = evaluate_document(doc)
    assert rep.toc_entries == 0
    assert rep.toc_recall is None
    assert rep.num_headings == 1
