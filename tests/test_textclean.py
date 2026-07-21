"""ルールベース本文整形（textclean）の検証。"""

from __future__ import annotations

from fullocr.ir import BBox, Block, BlockType, Document, Page
from fullocr.pipeline.textclean import clean_document


def _body(pageno, ro, text, x0=0, y0=0):
    return Block(id=f"p{pageno}b{ro}", bbox=BBox(x0=x0, y0=y0, x1=500, y1=y0 + 30),
                 type=BlockType.BODY, text=text, reading_order=ro)


def test_joins_broken_paragraph_across_page():
    doc = Document(pages=[
        Page(number=1, width=1000, height=1400, blocks=[
            _body(1, 1, "この文はページの終わりで途中で切れて"),  # 文末で終わらない
        ]),
        Page(number=2, width=1000, height=1400, blocks=[
            _body(2, 1, "しまったが、本来は続いている。"),
        ]),
    ])
    clean_document(doc)
    # 1ページ目のブロックに連結され、2ページ目の本文は消える
    p1_text = "".join(b.text for b in doc.pages[0].blocks)
    assert "途中で切れてしまったが、本来は続いている。" in p1_text
    assert all(b.text.strip() == "" or b.type is not BlockType.BODY for b in doc.pages[1].blocks) \
        or len(doc.pages[1].blocks) == 0


def test_keeps_separate_paragraphs():
    doc = Document(pages=[Page(number=1, width=1000, height=1400, blocks=[
        _body(1, 1, "段落その一。"),   # 文末で終わる → 連結しない
        _body(1, 2, "段落その二。"),
    ])])
    clean_document(doc)
    texts = [b.text for b in doc.pages[0].blocks if b.type is BlockType.BODY]
    assert texts == ["段落その一。", "段落その二。"]


def test_drops_page_number_noise():
    doc = Document(pages=[Page(number=1, width=1000, height=1400, blocks=[
        _body(1, 1, "本文。"),
        _body(1, 2, "42"),      # ノンブル
    ])])
    clean_document(doc)
    texts = [b.text for b in doc.pages[0].blocks if b.type is BlockType.BODY]
    assert "42" not in texts
    assert "本文。" in texts


def test_heading_breaks_the_join():
    doc = Document(pages=[Page(number=1, width=1000, height=1400, blocks=[
        _body(1, 1, "文末でない本文"),
        Block(id="h", bbox=BBox(x0=0, y0=40, x1=500, y1=70), type=BlockType.HEADING,
              text="見出し", heading_level=1, reading_order=2),
        _body(1, 3, "見出し後の本文。"),
    ])])
    clean_document(doc)
    # 見出しをまたいで連結しない
    bodies = [b.text for b in doc.pages[0].ordered_blocks() if b.type is BlockType.BODY]
    assert "文末でない本文" in bodies
    assert "見出し後の本文。" in bodies
