"""IR → Obsidian 向け Markdown 出力。

生成物（out_dir 配下）:
  <book>/
    00_index.md         … タイトル + 目次(TOC)
    01_<章タイトル>.md   … 章ごと分割（split_by_heading_level 指定時）
    attachments/        … 切り出した挿絵画像
  もしくは分割しない場合は 1つの .md にまとめる。
"""

from __future__ import annotations

from pathlib import Path

from ..config import RenderConfig
from ..ir import Block, BlockType, Document, Page
from .toc import build_toc, collect_headings, slugify, toc_to_markdown

# 本文に出さないブロック種別（設定で頭注/柱を落とす場合）。
_HEADER_FOOTER = {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}


def _front_matter(doc: Document) -> str:
    m = doc.meta
    lines = ["---"]
    if m.title:
        lines.append(f"title: {m.title}")
    if m.author:
        lines.append(f"author: {m.author}")
    if m.source_pdf:
        lines.append(f"source: {m.source_pdf}")
    lines.append(f"pages: {len(doc.pages)}")
    lines.append("tags: [fullocr]")
    lines.append("---")
    return "\n".join(lines)


def _image_link(rel_path: str, cfg: RenderConfig, alt: str = "") -> str:
    if cfg.obsidian_wiklinks:
        return f"![[{rel_path}]]"
    return f"![{alt}]({rel_path})"


def _block_to_md(b: Block, cfg: RenderConfig) -> str | None:
    if cfg.drop_headers_footers and b.type in _HEADER_FOOTER:
        return None
    t = b.text.strip()
    if b.type is BlockType.HEADING:
        level = min(6, max(1, b.heading_level or 1))
        return f"{'#' * level} {t}"
    if b.type is BlockType.FIGURE:
        if b.image_path:
            return _image_link(b.image_path, cfg, alt="figure")
        # 画像未切り出し時はプレースホルダ（ページ画像がまだ無いケース）
        return f"> [!note] 図領域 (p.{b.id})  bbox=({int(b.bbox.x0)},{int(b.bbox.y0)})-({int(b.bbox.x1)},{int(b.bbox.y1)})"
    if b.type is BlockType.CAPTION:
        return f"*{t}*" if t else None
    if b.type is BlockType.LIST:
        return f"- {t}" if t else None
    if b.type is BlockType.FOOTNOTE:
        return f"> {t}" if t else None
    if b.type is BlockType.TABLE:
        return t or None
    if not t:
        return None
    return t  # BODY / OTHER


def _page_to_md(page: Page, cfg: RenderConfig) -> list[str]:
    chunks: list[str] = []
    for b in page.ordered_blocks():
        md = _block_to_md(b, cfg)
        if md:
            chunks.append(md)
    return chunks


def crop_figures(doc: Document, images_dir: Path, cfg: RenderConfig) -> int:
    """図ブロックをページ画像から切り出して保存し、image_path を埋める。

    ページ画像 (Page.image_path) が無い場合は何もしない（プレースホルダ出力になる）。
    Pillow が無い環境でも致命的にしない。
    戻り値: 切り出した枚数。
    """
    try:
        from PIL import Image
    except ImportError:
        return 0

    images_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for page in doc.pages:
        if not page.image_path or not Path(page.image_path).exists():
            continue
        with Image.open(page.image_path) as im:
            # bbox は IR の座標空間 (page.width x page.height)。ページ画像は別工程
            # （NDL のPDF描画dpiとは独立）で描かれるため解像度が一致しない場合があり、
            # 実解像度に合わせてスケールする。
            sx = im.width / page.width if page.width else 1.0
            sy = im.height / page.height if page.height else 1.0
            for b in page.blocks:
                if b.type is not BlockType.FIGURE:
                    continue
                box = (
                    max(0, int(b.bbox.x0 * sx)),
                    max(0, int(b.bbox.y0 * sy)),
                    min(im.width, int(b.bbox.x1 * sx)),
                    min(im.height, int(b.bbox.y1 * sy)),
                )
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                fname = f"p{page.number:04d}_{b.id}.png"
                im.crop(box).save(images_dir / fname)
                b.image_path = f"{cfg.images_dirname}/{fname}"
                count += 1
    return count


def _body_filename(doc: Document) -> str:
    """分割しない場合の唯一の本文ファイル名（拡張子なし）。_split_documents と一致させる。"""
    return slugify(doc.meta.title or "document")


def _heading_targets(doc: Document, cfg: RenderConfig) -> list[str]:
    """collect_headings と同じ順で、各見出しが書き込まれる本文ファイル名を返す。

    _split_documents のファイル割り当てロジックと一致させること（目次リンク先が
    実際の出力ファイルとズレないように）。
    """
    level = cfg.split_by_heading_level
    if level is None:
        single = _body_filename(doc)
        return [single for _ in collect_headings(doc)]

    targets: list[str] = []
    current_name = "00_preface"
    idx = 1
    for page in doc.pages:
        for b in page.ordered_blocks():
            if b.type is BlockType.HEADING and b.text.strip():
                if (b.heading_level or 1) <= level:
                    current_name = f"{idx:02d}_{slugify(b.text)}"
                    idx += 1
                targets.append(current_name)
    return targets


def _split_documents(doc: Document, cfg: RenderConfig) -> list[tuple[str, list[str]]]:
    """(ファイル名ベース, Markdown行) の並びに分割。

    split_by_heading_level=None なら 1ファイル。指定時は、その階層以上の見出しで
    新ファイルを開始する。
    """
    level = cfg.split_by_heading_level
    if level is None:
        body: list[str] = []
        for page in doc.pages:
            body.extend(_page_to_md(page, cfg))
        title = doc.meta.title or "document"
        return [(slugify(title), body)]

    sections: list[tuple[str, list[str]]] = []
    current_name = "00_preface"
    current: list[str] = []
    idx = 1

    def flush() -> None:
        nonlocal current
        if current:
            sections.append((current_name, current))
            current = []

    for page in doc.pages:
        for b in page.ordered_blocks():
            if (
                b.type is BlockType.HEADING
                and (b.heading_level or 1) <= level
                and b.text.strip()
            ):
                flush()
                current_name = f"{idx:02d}_{slugify(b.text)}"
                idx += 1
            md = _block_to_md(b, cfg)
            if md:
                current.append(md)
    flush()
    if not sections:  # 見出しが無かった場合の保険
        sections.append(("01_document", current))
    return sections


def render_document(doc: Document, out_dir: str | Path, cfg: RenderConfig | None = None) -> list[Path]:
    """IR を Markdown 群として out_dir に書き出す。書いたファイルパスを返す。"""
    cfg = cfg or RenderConfig()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 前回の生成物を掃除する。split_by_heading_level 等の設定を変えて再生成した
    # 場合に、古い分割ファイルが残り続けるのを防ぐ（`--no-engines` での再生成は
    # 想定された使い方のため、ここでの掃除は安全）。
    for old in out_dir.glob("*.md"):
        old.unlink()
    images_dir = out_dir / cfg.images_dirname
    if images_dir.is_dir():
        for old_img in images_dir.iterdir():
            if old_img.is_file():
                old_img.unlink()

    # 図の切り出し（ページ画像があれば）。
    crop_figures(doc, out_dir / cfg.images_dirname, cfg)

    written: list[Path] = []

    # 目次 index。各見出しの本文ファイルを解決し、ファイル横断リンクを張る。
    toc = build_toc(doc, _heading_targets(doc, cfg))
    index_parts: list[str] = []
    if cfg.front_matter:
        index_parts.append(_front_matter(doc))
    index_parts.append(f"# {doc.meta.title or 'Document'}\n")
    index_parts.append("## 目次\n")
    index_parts.append(toc_to_markdown(toc) or "*(見出しが検出されませんでした)*")
    index_md = "\n\n".join(index_parts) + "\n"
    index_path = out_dir / "00_index.md"
    index_path.write_text(index_md, encoding="utf-8")
    written.append(index_path)

    # 本文。
    for name, chunks in _split_documents(doc, cfg):
        path = out_dir / f"{name}.md"
        # 同名衝突回避。
        if path in written:
            path = out_dir / f"{name}_{len(written)}.md"
        content = "\n\n".join(chunks).strip() + "\n"
        path.write_text(content, encoding="utf-8")
        written.append(path)

    return written
