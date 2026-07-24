"""目次(TOC)精度の評価ハーネス。PDCAの「C（測る物差し）」。

保存済み IR（work/<book>/ir.json）から、OCRを再実行せずに次を測る:
  - 検出した目次ページ / 生成アウトライン（見出し数・レベル分布）
  - 印刷目次（目次ページの各行）から抽出した項目 と 生成見出し の一致度
      toc_recall    … 印刷目次の項目のうち、見出しとして拾えている割合
      heading_precision … 生成見出しのうち、印刷目次に載っている割合
  - 章番号の連番チェック（第1章→第3章 等）

一致度は「正規化した文字列の部分一致」で雑に測る（章タイトルの表記ゆれに寛容）。
数百冊を回して平均を追うことで、下流ロジック/プロンプト改善の効果を数値で見る。
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel

from .ir import BlockType, Document
from .pipeline import tocdetect

# 行末のページ番号・リーダー（…／...／・・・）を落として目次項目の見出し名だけにする。
_TAIL = re.compile(r"\s*(?:\.{2,}|…+|・{3,})?\s*[0-9０-９]{1,4}\s*$")
_LEADERS = re.compile(r"(?:\.{2,}|…+|・{3,})")


class EvalReport(BaseModel):
    book: str | None = None
    pages: int = 0
    toc_pages: list[int] = []
    num_headings: int = 0
    level_hist: dict[int, int] = {}
    toc_entries: int = 0
    matched: int = 0
    missing: list[str] = []   # 印刷目次にあるが見出しに無い
    extra: list[str] = []     # 見出しだが印刷目次に無い
    toc_recall: float | None = None
    heading_precision: float | None = None
    sequence_issues: int = 0


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def _headings(doc: Document) -> list[str]:
    return [b.text.strip() for page in doc.pages for b in page.ordered_blocks()
            if b.type is BlockType.HEADING and b.text.strip()]


def printed_toc_titles(doc: Document) -> list[str]:
    """印刷目次ページの各行から、項目名（ページ番号・リーダーを除去）を取り出す。"""
    wanted = set(doc.meta.toc_pages or [])
    titles: list[str] = []
    for page in doc.pages:
        if page.number not in wanted:
            continue
        for b in page.ordered_blocks():
            if b.type not in (BlockType.BODY, BlockType.HEADING, BlockType.LIST):
                continue
            t = b.text.strip()
            if not t:
                continue
            t = _LEADERS.sub(" ", _TAIL.sub("", t)).strip()
            # 「目次」見出しそのものや短すぎる断片は除く。
            if len(t) >= 2 and not re.fullmatch(r"目\s*次|もくじ|contents", t, re.IGNORECASE):
                titles.append(t)
    return titles


def _matches(entry: str, headings_norm: list[str]) -> bool:
    e = _norm(entry)
    if len(e) < 2:
        return False
    return any(e in h or h in e for h in headings_norm if len(h) >= 2)


def evaluate_document(doc: Document, book: str | None = None) -> EvalReport:
    headings = _headings(doc)
    headings_norm = [_norm(h) for h in headings]
    toc_titles = printed_toc_titles(doc)
    toc_norm_seen: set[str] = set()

    matched = 0
    missing: list[str] = []
    for entry in toc_titles:
        key = _norm(entry)
        if key in toc_norm_seen:
            continue
        toc_norm_seen.add(key)
        if _matches(entry, headings_norm):
            matched += 1
        else:
            missing.append(entry)
    uniq_toc = len(toc_norm_seen)

    toc_titles_norm = [_norm(t) for t in toc_titles]
    extra = [h for h, hn in zip(headings, headings_norm)
             if not any(hn in t or t in hn for t in toc_titles_norm if len(t) >= 2)]

    seq = tocdetect.heading_sequence_report(doc)
    seq_issues = 0 if not seq else seq.count("\n- ") + (1 if "- " in seq else 0)

    return EvalReport(
        book=book,
        pages=len(doc.pages),
        toc_pages=list(doc.meta.toc_pages or []),
        num_headings=len(headings),
        level_hist=dict(Counter(min(6, max(1, b.heading_level or 1))
                                for page in doc.pages for b in page.ordered_blocks()
                                if b.type is BlockType.HEADING and b.text.strip())),
        toc_entries=uniq_toc,
        matched=matched,
        missing=missing[:50],
        extra=extra[:50],
        toc_recall=round(matched / uniq_toc, 3) if uniq_toc else None,
        heading_precision=round((len(headings) - len(extra)) / len(headings), 3) if headings else None,
        sequence_issues=seq_issues,
    )
