"""印刷された「目次」ページの検出と、見出し番号の連番チェック（いずれも無料・規則ベース）。

- detect_toc_pages: 巻頭付近で「目次らしい」ページ（ページ番号で終わる行やリーダー…が
  多い、または「目次/もくじ/CONTENTS」の見出しがある）を推定して番号リストを返す。
  これが「目次がどこからどこまでか」の土台。非表示化(#3)・目次画像のLLM送付・見出し
  突き合わせの起点になる。
- heading_sequence_report: 「第1章の次が第3章」のような章番号の飛び・重複・逆行を
  規則ベースで洗い出す（LLM不要）。_structure_review.md に載せる。
"""

from __future__ import annotations

import re

from ..ir import BlockType, Document

# 行末がページ番号（半角/全角数字）で終わる。リーダー(…や...)を挟むことも多い。
_ENDS_IN_PAGENO = re.compile(r"[.…・\s]*[0-9０-９]{1,4}\s*$")
_LEADER = re.compile(r"(?:\.{2,}|…{1,}|・{3,})")
_TOC_TITLE = re.compile(r"^\s*(目\s*次|もくじ|contents)\s*$", re.IGNORECASE)
_INDEX_TITLE = re.compile(r"^\s*(索\s*引|さくいん|index)\s*$", re.IGNORECASE)

# 章番号の抽出（第N章 / 第N部 / 全角数字にも対応）。
_CHAP = re.compile(r"第\s*([0-9０-９]{1,3})\s*章")
_PART = re.compile(r"第\s*([0-9０-９]{1,3})\s*部")


def _to_int(s: str) -> int:
    return int(s.translate({0xFF10 + i: 0x30 + i for i in range(10)}))


def _page_line_texts(page) -> list[str]:
    return [b.text.strip() for b in page.ordered_blocks()
            if b.type in (BlockType.BODY, BlockType.HEADING, BlockType.LIST) and b.text.strip()]


def _toc_score(page) -> tuple[float, int, bool]:
    """(目次らしさ 0-1, 目次エントリ行数, タイトル有無) を返す。"""
    lines = _page_line_texts(page)
    if not lines:
        return 0.0, 0, False
    has_title = any(_TOC_TITLE.match(t) for t in lines)
    entries = sum(1 for t in lines if _ENDS_IN_PAGENO.search(t) or _LEADER.search(t))
    return entries / len(lines), entries, has_title


def detect_toc_pages(doc: Document, front_ratio: float = 0.2, min_entries: int = 4) -> list[int]:
    """印刷目次のページ番号を推定。巻頭 front_ratio 以内のページだけを対象にする。"""
    n = len(doc.pages)
    if n == 0:
        return []
    limit = max(1, int(n * front_ratio) + 1)
    found: list[int] = []
    for page in doc.pages[:limit]:
        score, entries, has_title = _toc_score(page)
        # 「エントリ行が多く割合も高い」か「目次タイトル＋数エントリ」なら目次ページ。
        if (entries >= min_entries and score >= 0.5) or (has_title and entries >= 2):
            found.append(page.number)
    return found


def detect_index_pages(doc: Document, back_ratio: float = 0.25, min_entries: int = 6) -> list[int]:
    """巻末「索引」ページの番号を推定。末尾 back_ratio 以内を対象にする。

    索引ページは「索引/さくいん/INDEX」の見出し以降、各行が語＋ページ番号で終わる
    密な行が多い。目次ページより行が密なので min_entries は高め。
    """
    n = len(doc.pages)
    if n == 0:
        return []
    start = max(0, n - max(1, int(n * back_ratio) + 1))
    found: list[int] = []
    seen_title = False
    for page in doc.pages[start:]:
        lines = _page_line_texts(page)
        if not lines:
            continue
        if any(_INDEX_TITLE.match(t) for t in lines):
            seen_title = True
        entries = sum(1 for t in lines if _ENDS_IN_PAGENO.search(t) or _LEADER.search(t))
        score = entries / len(lines)
        if (entries >= min_entries and score >= 0.5) or (seen_title and entries >= 3):
            found.append(page.number)
    return found


def _chapter_numbers(doc: Document) -> list[tuple[int, str]]:
    """見出しから章番号の並びを (番号, 見出し文) で抜き出す（読み順）。"""
    seq: list[tuple[int, str]] = []
    for page in doc.pages:
        for b in page.ordered_blocks():
            if b.type is not BlockType.HEADING or not b.text.strip():
                continue
            m = _CHAP.search(b.text)
            if m:
                seq.append((_to_int(m.group(1)), b.text.strip()))
    return seq


def heading_sequence_report(doc: Document) -> str | None:
    """章番号の飛び・重複・逆行を指摘する Markdown 文字列（無ければ None）。"""
    seq = _chapter_numbers(doc)
    if len(seq) < 2:
        return None
    issues: list[str] = []
    prev_num = None
    for num, text in seq:
        if prev_num is not None:
            if num == prev_num:
                issues.append(f"- 章番号の重複: 第{num}章 が続けて現れます（「{text}」）")
            elif num < prev_num:
                issues.append(f"- 章番号の逆行: 第{prev_num}章 の後に 第{num}章（「{text}」）")
            elif num > prev_num + 1:
                miss = "・".join(f"第{k}章" for k in range(prev_num + 1, num))
                issues.append(f"- 章番号の飛び: 第{prev_num}章 の次が 第{num}章（{miss} が抜けている可能性）")
        prev_num = num
    if not issues:
        return None
    return "## 見出し番号の連番チェック（自動）\n\n" + "\n".join(issues)
