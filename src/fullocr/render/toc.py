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
    children: list["TocEntry"] = field(default_factory=list)


def collect_headings(doc: Document) -> list[tuple[int, str, int]]:
    """(level, text, page) の並びで見出しを収集（読み順）。"""
    result: list[tuple[int, str, int]] = []
    for page in doc.pages:
        for b in page.ordered_blocks():
            if b.type is BlockType.HEADING and b.text.strip():
                result.append((b.heading_level or 1, b.text.strip(), page.number))
    return result


def build_toc(doc: Document) -> list[TocEntry]:
    """フラットな見出し列を階層ツリーへ。"""
    flat = collect_headings(doc)
    root: list[TocEntry] = []
    stack: list[TocEntry] = []
    for level, text, page in flat:
        entry = TocEntry(level=level, text=text, page=page, slug=slugify(text))
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            stack[-1].children.append(entry)
        else:
            root.append(entry)
        stack.append(entry)
    return root


def toc_to_markdown(entries: list[TocEntry], *, link: bool = True) -> str:
    """階層目次を Markdown の入れ子リストにする。"""
    lines: list[str] = []

    def walk(items: list[TocEntry], depth: int) -> None:
        for e in items:
            indent = "  " * depth
            label = f"[[#{e.text}]]" if link else e.text
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
