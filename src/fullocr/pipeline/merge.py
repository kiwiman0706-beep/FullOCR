"""NDL テキスト × Marker 構造 のマージ → IR 生成。

方針:
  - テキストは NDL、意味的種別・読み順・図領域は Marker。
  - 座標系の違い（NDL=画像px / Marker=別スケール）は各ページ幅高さで 0..1 に
    正規化して吸収する。最終 IR の座標は NDL 画像px 空間に統一（図の切り出しに使う）。
  - Marker が取りこぼした NDL 行は本文ブロックとして救済（テキスト欠落を防ぐ）。
  - Marker が無い / 失敗した場合は NDL だけで IR を組む（縮退動作）。
"""

from __future__ import annotations

import re

from ..config import PipelineConfig
from ..ir import BBox, Block, BlockType, DocMeta, Document, Orientation, Page
from .layout import LayoutBlock, LayoutPage
from .ndl import NDLLine, NDLPage

# 「極論1」のような短いラベル＋番号だけのブロック（実務書によくある、枠で強調した
# 要約リストの通し番号）を検出する。実データでは、こうしたラベルと要約本文が別々の
# BODY ブロックとして検出され、箇条書きとして構造化されずに分離してしまっていた。
_LABEL_LIKE = re.compile(r"^[^\d\s]{1,6}\s*\d{1,2}$")


def _norm_bbox(b: BBox, w: float, h: float) -> BBox:
    if w <= 0 or h <= 0:
        return b
    return BBox(x0=b.x0 / w, y0=b.y0 / h, x1=b.x1 / w, y1=b.y1 / h)


def _denorm_bbox(b: BBox, w: float, h: float) -> BBox:
    return BBox(x0=b.x0 * w, y0=b.y0 * h, x1=b.x1 * w, y1=b.y1 * h)


# ページ上下端のこの割合を、柱・ノンブル（頁番号）が置かれる領域とみなす。
_HEADER_FOOTER_MARGIN = 0.08


def _rescued_line_type(line: NDLLine, page_h: float) -> BlockType:
    """構造エンジンのブロックに割り当たらなかった NDL 行の種別を推定する。

    これまでは一律 BODY 扱いだったため、柱・ノンブルが本文に混入していた
    （実本検証で判明）。ページ上端/下端の余白にある行はヘッダー/フッターとして
    分類し直し、drop_headers_footers による除外の対象に含める。
    """
    if page_h <= 0:
        return BlockType.BODY
    if line.bbox.y1 <= page_h * _HEADER_FOOTER_MARGIN:
        return BlockType.PAGE_HEADER
    if line.bbox.y0 >= page_h * (1 - _HEADER_FOOTER_MARGIN):
        return BlockType.PAGE_FOOTER
    return BlockType.BODY


def _order_lines(lines: list[NDLLine]) -> list[NDLLine]:
    """ブロック内の行を読み順に並べる（縦書き=右→左, 横書き=上→下）。"""
    if not lines:
        return lines
    vertical = sum(1 for ln in lines if ln.orientation is Orientation.VERTICAL) > len(lines) / 2
    if vertical:
        # 縦書き: 列は右から左、列内は上から下
        return sorted(lines, key=lambda ln: (-ln.bbox.cx, ln.bbox.cy))
    return sorted(lines, key=lambda ln: (ln.bbox.cy, ln.bbox.cx))


def _join_text(lines: list[NDLLine]) -> str:
    ordered = _order_lines(lines)
    # 日本語は行間結合で空白を入れない（英数字主体なら空白の方が良いが簡易に連結）。
    return "".join(ln.text for ln in ordered)


def _majority_orientation(lines: list[NDLLine]) -> Orientation:
    v = sum(1 for ln in lines if ln.orientation is Orientation.VERTICAL)
    return Orientation.VERTICAL if v > len(lines) / 2 else Orientation.HORIZONTAL


def merge_page(
    number: int,
    ndl: NDLPage | None,
    marker: LayoutPage | None,
    cfg: PipelineConfig,
) -> Page:
    """1ページ分をマージして IR Page を作る。"""
    ndl = ndl or NDLPage()
    min_conf = cfg.ndl.min_confidence
    ndl.lines = [ln for ln in ndl.lines if ln.confidence is None or ln.confidence >= min_conf]
    # 出力座標系は NDL 画像px。NDL サイズ不明なら Marker から借用。
    pw = ndl.width or (marker.width if marker else 0.0)
    ph = ndl.height or (marker.height if marker else 0.0)
    page = Page(number=number, width=pw, height=ph)

    threshold = cfg.assign_overlap_threshold

    # --- Marker が無い場合: NDL だけで組む（1行1ブロックを位置順に） ---
    if marker is None or not marker.blocks:
        ordered = _order_lines(ndl.lines)
        for i, line in enumerate(ordered, start=1):
            page.blocks.append(
                Block(
                    id=f"p{number}_b{i}",
                    bbox=line.bbox,
                    type=BlockType.BODY,
                    text=line.text,
                    orientation=line.orientation,
                    reading_order=i,
                    confidence=line.confidence,
                    source="ndl",
                )
            )
        return page

    # --- NDL 行を Marker ブロックへ割り当て（正規化オーバーラップ最大） ---
    m_norm = [_norm_bbox(mb.bbox, marker.width, marker.height) for mb in marker.blocks]
    assigned: dict[int, list[NDLLine]] = {i: [] for i in range(len(marker.blocks))}
    unassigned: list[NDLLine] = []

    for line in ndl.lines:
        ln = _norm_bbox(line.bbox, ndl.width or pw, ndl.height or ph)
        best_i, best_ratio = -1, 0.0
        for i, mb in enumerate(m_norm):
            inter = ln.intersection_area(mb)
            if inter <= 0:
                continue
            ratio = inter / ln.area if ln.area > 0 else 0.0
            if ratio > best_ratio:
                best_i, best_ratio = i, ratio
        if best_i >= 0 and best_ratio >= threshold:
            assigned[best_i].append(line)
        else:
            unassigned.append(line)

    # --- Marker ブロック順に IR ブロックを生成 ---
    for i, mb in sorted(enumerate(marker.blocks), key=lambda kv: kv[1].reading_order):
        lines = assigned[i]
        # NDL 座標系での bbox（Marker bbox を正規化→NDL px へ）
        bbox_ndl = _denorm_bbox(_norm_bbox(mb.bbox, marker.width, marker.height), pw, ph)
        if mb.type is BlockType.FIGURE:
            # 図: テキストは持たず領域のみ（後段でページ画像から切り出し）
            page.blocks.append(
                Block(
                    id=f"p{number}_b{mb.reading_order}",
                    bbox=bbox_ndl,
                    type=BlockType.FIGURE,
                    reading_order=mb.reading_order,
                    source="marker",
                )
            )
            continue
        if not lines:
            # テキストが割り当たらなかった非図ブロックは捨てる（柱/装飾等）
            continue
        if mb.type is BlockType.TABLE and mb.table_cells:
            text = _render_table_markdown(
                mb, lines, marker.width, marker.height, ndl.width or pw, ndl.height or ph
            )
        else:
            text = _join_text(lines)
        page.blocks.append(
            Block(
                id=f"p{number}_b{mb.reading_order}",
                bbox=bbox_ndl,
                type=mb.type,
                text=text,
                orientation=_majority_orientation(lines),
                reading_order=mb.reading_order,
                heading_level=mb.heading_level if mb.type is BlockType.HEADING else None,
                confidence=_avg_conf(lines),
                source="merged",
            )
        )

    # --- 取りこぼした NDL 行の救済（末尾に付与） ---
    if unassigned:
        base = max((b.reading_order for b in page.blocks), default=0)
        for j, line in enumerate(_order_lines(unassigned), start=1):
            page.blocks.append(
                Block(
                    id=f"p{number}_u{j}",
                    bbox=line.bbox,
                    type=_rescued_line_type(line, ph),
                    text=line.text,
                    orientation=line.orientation,
                    reading_order=base + j,
                    confidence=line.confidence,
                    source="ndl-rescue",
                )
            )

    page.blocks = _merge_label_pairs(page.blocks)
    return page


def _merge_label_pairs(blocks: list[Block]) -> list[Block]:
    """「極論1」のような通し番号ラベルだけのブロックを、同じ行（bbox の縦位置が
    重なる）にある本文ブロックと1つの箇条書き(LIST)ブロックに合体させる。

    実データでは、枠で強調した要約リストの「ラベル列」と「本文列」が2カラム
    レイアウトになっており、Docling の読み順ではラベルが本文とは離れた場所に
    まとめて出てくる（例: 本文1〜4が先、ラベル1〜4が後）。そのため読み順での
    隣接ではなく、bbox の縦位置（行）が一致するかどうかで対応付ける。
    """
    ordered = sorted(blocks, key=lambda b: b.reading_order)
    label_idxs = [
        i for i, b in enumerate(ordered) if b.type is BlockType.BODY and _LABEL_LIKE.match(b.text.strip())
    ]
    if not label_idxs:
        return blocks

    used: set[int] = set()
    pair_of: dict[int, int] = {}
    for li in label_idxs:
        label = ordered[li]
        best_j, best_dist = None, None
        for j, cand in enumerate(ordered):
            if j == li or j in used or j in label_idxs or cand.type is not BlockType.BODY:
                continue
            row_tol = max(label.bbox.height, cand.bbox.height) * 0.6
            dist = abs(cand.bbox.cy - label.bbox.cy)
            if dist > row_tol:
                continue
            if best_dist is None or dist < best_dist:
                best_dist, best_j = dist, j
        if best_j is not None:
            pair_of[li] = best_j
            used.add(best_j)

    merged: list[Block] = []
    for i, b in enumerate(ordered):
        if i in used:
            continue
        if i in pair_of:
            content = ordered[pair_of[i]]
            merged.append(
                Block(
                    id=b.id,
                    bbox=b.bbox,
                    type=BlockType.LIST,
                    text=f"{b.text.strip()} {content.text.strip()}",
                    orientation=b.orientation,
                    reading_order=min(b.reading_order, content.reading_order),
                    confidence=b.confidence,
                    source=b.source,
                )
            )
            continue
        merged.append(b)
    merged.sort(key=lambda b: b.reading_order)
    return merged


def _render_table_markdown(
    mb: LayoutBlock,
    lines: list[NDLLine],
    marker_w: float,
    marker_h: float,
    ndl_w: float,
    ndl_h: float,
) -> str:
    """table_cells（行・列グリッド）に NDL 行を割り当て、Markdown 表を組み立てる。

    セル単位の再割り当ては、テーブル全体に既に割り当て済みの行の中からだけ行う
    （重い全行スキャンを避ける）。rowspan/colspan は左上セルにのみテキストを置く
    簡易表現（Markdownの表そのものが結合セルを表現できないため）。
    """
    cells = mb.table_cells or []
    if not cells:
        return _join_text(lines)

    cell_norm = [(_norm_bbox(c["bbox"], marker_w, marker_h), c) for c in cells]
    grid: dict[tuple[int, int], list[NDLLine]] = {}
    for line in lines:
        ln = _norm_bbox(line.bbox, ndl_w, ndl_h)
        best, best_ratio = None, 0.0
        for cb, c in cell_norm:
            inter = ln.intersection_area(cb)
            if inter <= 0:
                continue
            ratio = inter / ln.area if ln.area > 0 else 0.0
            if ratio > best_ratio:
                best_ratio, best = ratio, c
        if best is not None:
            grid.setdefault((best["row"], best["col"]), []).append(line)

    n_rows = max((c["row"] + c["row_span"] for c in cells), default=0)
    n_cols = max((c["col"] + c["col_span"] for c in cells), default=0)
    if n_rows == 0 or n_cols == 0:
        return _join_text(lines)

    table = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    for c in cells:
        text = _join_text(_order_lines(grid.get((c["row"], c["col"]), [])))
        table[c["row"]][c["col"]] = text.replace("|", "\\|")

    rows_md = ["| " + " | ".join(row) + " |" for row in table]
    rows_md.insert(1, "| " + " | ".join("---" for _ in range(n_cols)) + " |")
    return "\n".join(rows_md)


def _avg_conf(lines: list[NDLLine]) -> float | None:
    vals = [ln.confidence for ln in lines if ln.confidence is not None]
    return sum(vals) / len(vals) if vals else None


def assign_heading_levels(doc: Document, max_levels: int = 3) -> None:
    """見出しレベル未確定のものを、見出しブロックの高さ順で推定して埋める。

    エンジン（Docling/Marker/MinerU）が level を与えていればそれを尊重し、無いもの
    だけ高さから補完する。

    実本検証では、Docling は section_header 全件に level=1、MinerU は level=2 を
    一律に付けており、これを高さベースの推定で上書きする実験もしたが、装飾的な
    表紙テキストや見出しの多さ（章タイトルは200件中11件程度）に対して bbox の
    高さだけでは「真の章タイトル」を十分に絞り込めず、むしろ誤った親子関係を
    もつ目次を作ってしまうことを確認したため見送った（フラットな目次の方が
    正直で実害が少ない）。将来 GUI での手動補正 or 章タイトル固有の追加シグナル
    （例: ページ内の位置、直前に大きい余白があるか等）が使えるようになってから
    再検討する。
    """
    heights: list[float] = [
        b.bbox.height
        for p in doc.pages
        for b in p.blocks
        if b.type is BlockType.HEADING and b.heading_level is None and b.bbox.height > 0
    ]
    if not heights:
        return
    hi = max(heights)
    lo = min(heights)
    span = (hi - lo) or 1.0
    for p in doc.pages:
        for b in p.blocks:
            if b.type is BlockType.HEADING and b.heading_level is None:
                # 高いほどレベル小（=上位見出し）。1..max_levels に量子化。
                rank = (hi - b.bbox.height) / span  # 0(最大高)→1(最小高)
                b.heading_level = 1 + min(max_levels - 1, int(rank * max_levels))


def build_document(
    ndl_pages: list[NDLPage],
    marker_pages: list[LayoutPage],
    cfg: PipelineConfig,
    meta: DocMeta | None = None,
) -> Document:
    """ページ列同士をマージして 1 冊分の IR を構築。

    ページ数がずれても、共通する範囲でマージし、余りは片側だけで補う。
    """
    n = max(len(ndl_pages), len(marker_pages))
    doc = Document(meta=meta or DocMeta())
    for i in range(n):
        ndl = ndl_pages[i] if i < len(ndl_pages) else None
        marker = marker_pages[i] if i < len(marker_pages) else None
        doc.pages.append(merge_page(i + 1, ndl, marker, cfg))
    assign_heading_levels(doc)
    return doc
