"""目次(TOC)構築。IR の見出しブロックから階層目次を作る。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ir import BlockType, Document


@dataclass
class TocEntry:
    level: int
    text: str
    page: int
    slug: str
    target_file: str | None = None  # この見出しが書かれた本文ファイル名(拡張子なし)
    children: list["TocEntry"] = field(default_factory=list)


def collect_headings(doc: Document) -> list[tuple[int, str, int]]:
    """(level, text, page) の並びで見出しを収集（読み順）。"""
    result: list[tuple[int, str, int]] = []
    for page in doc.pages:
        for b in page.ordered_blocks():
            if b.type is BlockType.HEADING and b.text.strip():
                result.append((b.heading_level or 1, b.text.strip(), page.number))
    return result


def build_toc(doc: Document, targets: list[str | None] | None = None) -> list[TocEntry]:
    """フラットな見出し列を階層ツリーへ。

    targets は collect_headings と同じ順の「各見出しが書かれた本文ファイル名」。
    与えると各 TocEntry に target_file を紐付け、ファイル横断リンクを張れる。
    """
    flat = collect_headings(doc)
    if targets is None or len(targets) != len(flat):
        targets = [None] * len(flat)
    root: list[TocEntry] = []
    stack: list[TocEntry] = []
    for (level, text, page), target in zip(flat, targets):
        entry = TocEntry(level=level, text=text, page=page, slug=slugify(text), target_file=target)
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            stack[-1].children.append(entry)
        else:
            root.append(entry)
        stack.append(entry)
    return root


# wikilink の [[...]] / [[...#...]] を壊す文字。見出しにこれが含まれると
# ファイル横断リンクのアンカー指定ができないので、その場合はファイル先頭リンクへ劣化。
_ANCHOR_BREAK = set("[]#|^")


def _toc_link(entry: TocEntry) -> str:
    """TocEntry を Obsidian wikilink（表示テキスト付き）にする。"""
    text = entry.text
    file = entry.target_file
    anchor_safe = not (_ANCHOR_BREAK & set(text))
    if file:
        if anchor_safe:
            # 本文ファイルの該当見出しへジャンプ
            return f"[[{file}#{text}]]"
        # 見出しに []#|^ を含む → 見出しジャンプ不可。ファイル先頭へ飛ばし、
        # 表示は記号を除いた読みやすい別名にする。
        display = "".join(c for c in text if c not in _ANCHOR_BREAK).strip()
        return f"[[{file}|{display}]]" if display else f"[[{file}]]"
    # ファイル不明（従来動作）: 同一ファイル内アンカー
    return f"[[#{text}]]" if anchor_safe else text


def toc_to_markdown(entries: list[TocEntry], *, link: bool = True) -> str:
    """階層目次を Markdown の入れ子リストにする。"""
    lines: list[str] = []

    def walk(items: list[TocEntry], depth: int) -> None:
        for e in items:
            indent = "  " * depth
            label = _toc_link(e) if link else e.text
            lines.append(f"{indent}- {label}")
            walk(e.children, depth + 1)

    walk(entries, 0)
    return "\n".join(lines)


def slugify(text: str) -> str:
    """ファイル名/アンカー用の簡易スラグ（日本語はそのまま、記号を除去）。"""
    bad = '<>:"/\\|?*\n\t'
    s = "".join(("_" if c in bad else c) for c in text).strip()
    s = "_".join(s.split())
    return s[:80] or "section"
