"""ルールベースの本文整形（LLM 不要・決定的）。

Markdown にはページの概念が無いのに、OCR/レイアウト解析はページ単位でブロックを
切る。その結果「文の途中でページが変わって段落がぶつ切り」「柱・ノンブルの残骸が
本文に紛れる」といったノイズが出る。ここを機械的に直す（config.text_cleanup で ON/OFF）。

やること:
  - 連続する本文(BODY)ブロックのうち、前ブロックが文末（。！？」）等）で終わって
    いないものは、次の本文ブロックと連結する（ページ境界をまたいでも連結）。
  - ノンブル/柱の残骸（数字のみ、ごく短い記号列）を本文から除去。
  - ブロック内の不要な空白・改行を正規化。

見出し・図・キャプション・表など本文以外は触らない（構造は保持）。
"""

from __future__ import annotations

import re

from ..ir import Block, BlockType, Document

# 文末とみなす文字（これで終わっていれば段落の区切り＝連結しない）。
_SENTENCE_END = "。．！？…」』）】〉》”\"’'.!?)"
# ノンブル/柱の残骸: 数字のみ / ローマ数字のみ / ごく短い記号列。
_NOISE_RE = re.compile(r"^[\s0-9ivxlcIVXLC・．.\-—―]+$")


def _ends_sentence(text: str) -> bool:
    t = text.rstrip()
    return bool(t) and t[-1] in _SENTENCE_END


def _is_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    # 数字・記号だけ、かつ短い → ノンブル等
    return len(t) <= 6 and bool(_NOISE_RE.match(t))


def _normalize(text: str) -> str:
    # 連続空白の圧縮・行頭行末の空白除去（日本語本文の体裁を整える）。
    return re.sub(r"[ \t　]+", " ", text).strip()


def clean_document(doc: Document) -> int:
    """doc をその場で整形。戻り値: 連結・除去で減らしたブロック数。

    ページをまたいで本文を連結するため、doc 全体を読み順で走査し、連結先は
    「直前の本文ブロック」（前ページのものでもよい）に追記する。
    """
    removed = 0
    prev_body: Block | None = None
    for page in doc.pages:
        kept: list[Block] = []
        for b in page.ordered_blocks():
            if b.type is BlockType.BODY:
                if _is_noise(b.text):
                    removed += 1
                    continue
                b.text = _normalize(b.text)
                if prev_body is not None and not _ends_sentence(prev_body.text):
                    # 直前本文が文末で終わっていない → 続き。連結する。
                    prev_body.text = (prev_body.text + b.text).strip()
                    removed += 1
                    continue
                kept.append(b)
                prev_body = b
            else:
                kept.append(b)
                # 見出し・図等をまたいだら本文の連結は途切れる。
                prev_body = None
        # reading_order を振り直す。
        for i, b in enumerate(kept, start=1):
            b.reading_order = i
        page.blocks = kept
    return removed
