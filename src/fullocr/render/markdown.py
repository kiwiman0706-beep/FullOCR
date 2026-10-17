"""IR → Obsidian 向け Markdown 出力。

生成物（out_dir 配下）:
  <book>/
    00_index.md         … タイトル + 目次(TOC)
    01_<章タイトル>.md   … 章ごと分割（split_by_heading_level 指定時）
    attachments/        … 切り出した挿絵画像
  もしくは分割しない場合は 1つの .md にまとめる。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import RenderConfig
from ..ir import Block, BlockType, Document, Page
from .toc import build_toc, collect_headings, slugify, toc_to_markdown

# 本文に出さないブロック種別（設定で頭注/柱を落とす場合）。
_HEADER_FOOTER = {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}

# ページアンカーのスキーム識別子（dsbooks 等の外部リーダーとの契約。docs/page-anchor-spec.md）。
PAGE_ANCHOR_SCHEME = "fullocr-1"
# 柱・ノンブルから印刷ページ番号を拾うための断片。全角数字は正規化してから当てる。
_NOMBRE_ARABIC = re.compile(r"^\d{1,4}$")
_NOMBRE_ROMAN = re.compile(r"^[ivxlcdm]{1,6}$", re.IGNORECASE)
_ZEN2HAN = str.maketrans("０１２３４５６７８９", "0123456789")


def _detect_nombre(page: Page) -> str | None:
    """ページの柱/ノンブル（PAGE_HEADER/PAGE_FOOTER）から印刷ページ番号を推定。

    フッタを優先し、無ければヘッダ。柱に章タイトル＋番号が同居するので、空白等で
    分割した「数字だけ」「ローマ数字だけ」のトークンを拾う。見つからなければ None。
    本文から除外(drop_headers_footers)されていても IR には残っているので参照できる。
    """
    for want in (BlockType.PAGE_FOOTER, BlockType.PAGE_HEADER):
        for b in page.blocks:
            if b.type is not want:
                continue
            for tok in b.text.translate(_ZEN2HAN).split():
                tok = tok.strip(".,)(-–—　")
                if _NOMBRE_ARABIC.match(tok):
                    return str(int(tok))  # 先頭0を落として正規化
                if _NOMBRE_ROMAN.match(tok):
                    return tok.lower()
    return None


def _page_offset(doc: Document) -> int | None:
    """pdf_page = printed_page + offset の offset を、算用数字ノンブルの最頻差で推定。

    前付け(ローマ数字)や番号なしページは無視。3ページ以上で一致した差があるときだけ
    返す（自炊は見開き2up等で崩れることがあるため、確信のある時だけ front-matter に出す）。
    """
    from collections import Counter

    diffs: Counter[int] = Counter()
    for page in doc.pages:
        n = _detect_nombre(page)
        if n and n.isdigit():
            diffs[page.number - int(n)] += 1
    if not diffs:
        return None
    offset, count = diffs.most_common(1)[0]
    return offset if count >= 3 else None


def _page_anchor(page: Page, cfg: RenderConfig) -> str | None:
    """ページ境界の不可視マーカー（HTMLコメント）。アウトラインには出ない。

    例: <!-- fullocr:page pdf=23 print="15" -->
    pdf= は原PDFの物理ページ(1始まり, 常にあり / #page=N で開ける)。
    print= はOCRで拾えた印刷ページ番号(任意, ローマ数字や欠落あり)。
    """
    if not cfg.page_anchors:
        return None
    printed = _detect_nombre(page)
    attr = f' print="{printed}"' if printed else ""
    return f"<!-- fullocr:page pdf={page.number}{attr} -->"


def _front_matter(doc: Document, cfg: RenderConfig | None = None) -> str:
    m = doc.meta
    lines = ["---"]
    if m.title:
        lines.append(f"title: {m.title}")
    if m.author:
        lines.append(f"author: {m.author}")
    # 書誌メタ（biblio 取得時のみ）。Obsidian のプロパティとして検索・Dataview に使える。
    if m.publisher:
        lines.append(f"publisher: {m.publisher}")
    if m.published:
        lines.append(f"published: {m.published}")
    if m.isbn:
        lines.append(f"isbn: {m.isbn}")
    if m.cover_url:
        lines.append(f"cover: {m.cover_url}")
    if m.categories:
        lines.append("categories: [" + ", ".join(m.categories) + "]")
    if m.source_pdf:
        lines.append(f"source: {m.source_pdf}")
    lines.append(f"pages: {m.page_count or len(doc.pages)}")
    # ページアンカー対応リーダー（dsbooks 等）向けの宣言。docs/page-anchor-spec.md。
    if cfg and cfg.page_anchors:
        lines.append(f"page_anchors: {PAGE_ANCHOR_SCHEME}")
        if m.pdf_name:
            lines.append(f"pdf_file: {m.pdf_name}")
        offset = _page_offset(doc)
        if offset is not None:
            lines.append(f"page_offset: {offset}")
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
            link = _image_link(b.image_path, cfg, alt=t or "figure")
            # 代替テキスト(LLM生成)があれば、画像の下に説明を添える。Obsidian の
            # wikilink 埋め込みは alt を表示・検索できないため、本文として出して
            # スマホでの全文検索に引っかかるようにする。
            return f"{link}\n*{t}*" if t else link
        # 画像未切り出し時はプレースホルダ（ページ画像がまだ無いケース）
        return f"> [!note] 図領域 (p.{b.id})  bbox=({int(b.bbox.x0)},{int(b.bbox.y0)})-({int(b.bbox.x1)},{int(b.bbox.y1)})"
    if b.type is BlockType.CAPTION:
        return f"*{t}*" if t else None
    if b.type is BlockType.LIST:
        return f"- {t}" if t else None
    if b.type is BlockType.FOOTNOTE:
        return f"> {t}" if t else None
    if b.type is BlockType.TABLE:
        # 表は画像で原形を保つ（構造が崩れると意味が変わるため）。切り出せていれば
        # 画像＋検索用キャプション（中のOCRテキスト）。無理なら Markdown 表/テキスト。
        if cfg.tables_as_images and b.image_path:
            link = _image_link(b.image_path, cfg, alt=t or "table")
            return f"{link}\n*{t}*" if t else link
        return t or None
    if not t:
        return None
    return t  # BODY / OTHER


def _hidden_pages(doc: Document, cfg: RenderConfig) -> set[int]:
    """本文でコメントアウトして隠すページ番号（印刷目次ページ）。"""
    return set(doc.meta.toc_pages) if cfg.hide_toc_pages else set()


def _maybe_comment(md: str, hide: bool) -> str:
    """hide が真なら Obsidian のコメント（%% … %%）で囲んで非表示にする。"""
    return f"%%\n{md}\n%%" if hide else md


def _page_link(page: Page, cfg: RenderConfig, pdf_name: str | None) -> str:
    """Obsidian の PDF ページリンク（該当ページの原文を開く）。無効時は空。"""
    if not (cfg.pdf_page_links and pdf_name):
        return ""
    return f"  [[{pdf_name}#page={page.number}|📄原文 p.{page.number}]]"


def _page_to_md(
    page: Page, cfg: RenderConfig, hidden: set[int] | None = None, pdf_name: str | None = None
) -> list[str]:
    hidden = hidden or set()
    hide = page.number in hidden
    chunks: list[str] = []
    anchor = _page_anchor(page, cfg)
    if anchor:
        chunks.append(anchor)  # 不可視マーカーは隠しページでもコメント化せず常に出す
    for b in page.ordered_blocks():
        md = _block_to_md(b, cfg)
        if not md:
            continue
        if b.type is BlockType.HEADING:
            md += _page_link(page, cfg, pdf_name)
        chunks.append(_maybe_comment(md, hide))
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
    # 図は常に、表は tables_as_images のとき画像として切り出す。
    crop_types = {BlockType.FIGURE}
    if cfg.tables_as_images:
        crop_types.add(BlockType.TABLE)
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
                if b.type not in crop_types:
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
    hidden = _hidden_pages(doc, cfg)
    pdf_name = doc.meta.pdf_name
    if level is None:
        body: list[str] = []
        for page in doc.pages:
            body.extend(_page_to_md(page, cfg, hidden, pdf_name))
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
        hide = page.number in hidden
        anchor_pending = _page_anchor(page, cfg)  # そのページ最初の可視ブロックの直前に置く
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
                if anchor_pending:
                    current.append(anchor_pending)  # flush後＝同じ章ファイルに入る
                    anchor_pending = None
                if b.type is BlockType.HEADING:
                    md += _page_link(page, cfg, pdf_name)
                current.append(_maybe_comment(md, hide))
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
        index_parts.append(_front_matter(doc, cfg))
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
