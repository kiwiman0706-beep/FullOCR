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
from dataclasses import replace

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


# 構造エンジンの数式テキストが LaTeX か（enrich_formula 無しだと OCR 文字列のまま）。
_LATEX_LIKE = re.compile(r"\\[A-Za-z]+|[_^]\s*\{")

# これより小さい（ページ面積比）テキスト無しの図は装飾アイコンとして捨てる。
_MIN_FIGURE_AREA = 0.003

def _figure_text(lines: list[NDLLine]) -> str:
    """図の中の文字（1行ずつ）。手順番号バッジ「5」「1.」のような文字の無いものは捨てる
    （実本「できるPowerAutomate」で図ごとに数字だけの枠が並んでいた）。"""
    text = "\n".join(ln.text for ln in _order_lines(lines))
    return text if len(re.sub(r"[\s\d\W_]", "", text)) >= 2 else ""


_NOMBRE_LINE = re.compile(r"^\s*[0-9０-９]{1,4}\s*$")


def _is_margin_nombre(line: NDLLine, page_h: float) -> bool:
    """上下余白にある数字だけの短い行（ノンブル候補）か。"""
    if page_h <= 0 or not _NOMBRE_LINE.match(line.text):
        return False
    return (line.bbox.y1 <= page_h * _HEADER_FOOTER_MARGIN
            or line.bbox.y0 >= page_h * (1 - _HEADER_FOOTER_MARGIN))


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


def _is_vertical(lines: list[NDLLine]) -> bool:
    return sum(1 for ln in lines if ln.orientation is Orientation.VERTICAL) > len(lines) / 2


def _split_columns(lines: list[NDLLine]) -> list[list[NDLLine]]:
    """横書きの行を、x 範囲が重ならない「段（カラム）」に分ける（左→右）。

    実本検証（CKD診療ガイド）で、2段組の左右に並んだ見出し「3. 人類の健康を…」
    「4. わが国における…」を Docling が1ブロックにまとめ、行を上→下で並べると
    2つの見出しの行が交互に混ざる事例があった。x 区間の重なりで行を段に束ねる。
    行が段をまたぐ（全幅の行がある）場合は1段のまま。
    """
    if len(lines) < 2:
        return [lines]
    srt = sorted(lines, key=lambda ln: ln.bbox.x0)
    cols: list[list[NDLLine]] = [[srt[0]]]
    right = srt[0].bbox.x1
    for ln in srt[1:]:
        # 段間の溝（行高の半分程度）より右から始まる行は新しい段。
        gap_tol = max(2.0, ln.bbox.height * 0.5)
        if ln.bbox.x0 > right + gap_tol:
            cols.append([ln])
        else:
            cols[-1].append(ln)
        right = max(right, ln.bbox.x1)
    return cols


def _true_columns(lines: list[NDLLine]) -> list[list[NDLLine]] | None:
    """「本当の段組」なら段ごとの行リストを返す。表のような行揃え（各段の行数が
    同じ＝氏名・所属の2列など）は段組とみなさず None（従来どおり行順で読む）。"""
    if _is_vertical(lines):
        return None
    cols = _split_columns(lines)
    if len(cols) < 2:
        return None
    if len({len(c) for c in cols}) == 1 and len(cols[0]) > 1:
        return None  # 行数が揃っている＝表形式の行並び
    return cols


def _order_lines(lines: list[NDLLine]) -> list[NDLLine]:
    """ブロック内の行を読み順に並べる（縦書き=右→左, 横書き=上→下、段組は段ごと）。"""
    if not lines:
        return lines
    if _is_vertical(lines):
        # 縦書き: 列は右から左、列内は上から下
        return sorted(lines, key=lambda ln: (-ln.bbox.cx, ln.bbox.cy))
    cols = _true_columns(lines)
    if cols:
        out: list[NDLLine] = []
        for col in cols:
            out.extend(sorted(col, key=lambda ln: (ln.bbox.cy, ln.bbox.cx)))
        return out
    return sorted(lines, key=lambda ln: (ln.bbox.cy, ln.bbox.cx))


# 目次行（リーダー罫や「……12」で終わる行）。これらは1行ずつ改行を保つ。
_TOC_LINE = re.compile(r"(?:\.{3,}|…{2,}|・{3,}|‥{2,}|—{3,})\s*[0-9０-９ivxlcIVXLC]*\s*$|(?:\.{3,}|…{2,}|‥{2,})")


_ENDS_PAGENO = re.compile(r"[0-9０-９]{1,4}\s*$")


def _join_text(lines: list[NDLLine]) -> str:
    ordered = _order_lines(lines)
    # 日本語は行間結合で空白を入れない（英数字主体なら空白の方が良いが簡易に連結）。
    # ただし目次のようなリーダー罫つきの行は、行ごとに改行を残す（1行に潰れると読めない）。
    # 縦書き目次はリーダー罫が無く「…6」とページ番号で終わるだけのことが多い（実本
    # 「3秒で心電図を読む本」）ので、行の過半が番号で終わるブロックも目次扱いにする。
    toc_block = len(ordered) >= 4 and sum(
        1 for ln in ordered if _ENDS_PAGENO.search(ln.text) or _TOC_LINE.search(ln.text)
    ) >= len(ordered) * 0.4
    parts: list[str] = []
    for i, ln in enumerate(ordered):
        if i > 0:
            prev = ordered[i - 1].text
            if toc_block or _TOC_LINE.search(prev) or _TOC_LINE.search(ln.text):
                parts.append("\n")
            elif prev[-1:].isascii() and prev[-1:].isalpha() and ln.text[:1].isascii() \
                    and ln.text[:1].isalpha():
                parts.append(" ")  # 英単語の行またぎは空白で区切る
        parts.append(ln.text)
    return "".join(parts)


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
    # 出力座標系は NDL 画像px。NDL サイズ不明なら Marker から借用。
    pw = ndl.width or (marker.width if marker else 0.0)
    ph = ndl.height or (marker.height if marker else 0.0)
    # 低信頼行は捨てる。ただし上下余白の短い数字（ノンブル）は NDL の信頼度が極端に
    # 低く出る（実本で 0.0〜0.3）ので残す。柱/ノンブル扱いで本文には出ず、印刷ページ
    # 番号の検出（page_anchors の print=）にだけ使われる。
    ndl.lines = [
        ln for ln in ndl.lines
        if ln.confidence is None or ln.confidence >= min_conf or _is_margin_nombre(ln, ph)
    ]
    page = Page(number=number, width=pw, height=ph)

    threshold = cfg.assign_overlap_threshold

    # --- 章扉ページ: 構造エンジンが何も検出せず、短い行が数行だけ ---
    title = _title_page_heading(number, ndl, marker, ph)
    if title is not None:
        page.blocks.append(title)
        return page

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

    # NDL の行順（JSON 内の並び）は NDL 自身の読み順推定。縦書きページでは
    # 構造エンジン（横書き前提）の読み順より信頼できるので、並べ替えの鍵に使う。
    line_idx = {id(ln): k for k, ln in enumerate(ndl.lines)}
    block_key: dict[str, int] = {}

    # --- Marker ブロック順に IR ブロックを生成 ---
    for i, mb in sorted(enumerate(marker.blocks), key=lambda kv: kv[1].reading_order):
        lines = assigned[i]
        if mb.type is BlockType.FORMULA:
            # LaTeX 化された数式だけ FORMULA として残す（NDL は数式・分数を崩すので
            # 本文の NDL テキストより LaTeX を採る）。LaTeX でなければ通常の本文扱い。
            if _LATEX_LIKE.search(mb.text):
                bid = f"p{number}_b{mb.reading_order}"
                page.blocks.append(Block(
                    id=bid,
                    bbox=_denorm_bbox(_norm_bbox(mb.bbox, marker.width, marker.height), pw, ph),
                    type=BlockType.FORMULA,
                    text=mb.text.strip(),
                    reading_order=mb.reading_order,
                    source="marker-latex",
                ))
                if lines:
                    block_key[bid] = min(line_idx[id(ln)] for ln in lines)
                continue
            mb = replace(mb, type=BlockType.BODY)
        # NDL 座標系での bbox（Marker bbox を正規化→NDL px へ）
        bbox_ndl = _denorm_bbox(_norm_bbox(mb.bbox, marker.width, marker.height), pw, ph)
        # 左右に並んだ別々の見出し/キャプションを1ブロックにまとめられた場合は段ごとに分割。
        if mb.type in (BlockType.HEADING, BlockType.CAPTION) and lines:
            cols = _true_columns(lines)
            if cols:
                for c, col in enumerate(cols):
                    bid = f"p{number}_b{mb.reading_order}" + (f"c{c}" if c else "")
                    page.blocks.append(
                        Block(
                            id=bid,
                            bbox=_lines_bbox(col),
                            type=mb.type,
                            text=_join_text(col),
                            orientation=_majority_orientation(col),
                            reading_order=mb.reading_order,
                            heading_level=mb.heading_level
                            if mb.type is BlockType.HEADING else None,
                            confidence=_avg_conf(col),
                            source="merged",
                        )
                    )
                    block_key[bid] = min(line_idx[id(ln)] for ln in col)
                continue
        if lines:
            block_key[f"p{number}_b{mb.reading_order}"] = min(line_idx[id(ln)] for ln in lines)
        if mb.type is BlockType.FIGURE:
            # 図: 領域のみ（後段でページ画像から切り出し）。テキストの無いごく小さな図は
            # 装飾アイコン（✕印・チェック枠など）なので捨てる（実本「救急当直」で多発）。
            if not lines and pw > 0 and ph > 0 and bbox_ndl.area < _MIN_FIGURE_AREA * pw * ph:
                continue
            # 図の中の文字（デザインされた章題バナー・吹き出し・図中ラベル）は以前は
            # 捨てていた。章題ごと消えるので、図に持たせて検索用に出す。
            page.blocks.append(
                Block(
                    id=f"p{number}_b{mb.reading_order}",
                    bbox=bbox_ndl,
                    type=BlockType.FIGURE,
                    reading_order=mb.reading_order,
                    source="marker",
                    # 図中の文字は別々のラベルなので1行ずつ（本文のように連結しない）。
                    ocr_text=_figure_text(lines),
                )
            )
            continue
        if not lines:
            # 柱/ノンブルは NDL が読まないことが多い（実本で 178 ページ中 1 ページしか
            # ノンブルを読まなかった）。印刷ページ番号の検出用に、構造エンジン側の
            # テキストで残す（本文には出ない）。それ以外の空ブロックは捨てる（装飾等）。
            if mb.type in (BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER) and mb.text.strip():
                page.blocks.append(
                    Block(
                        id=f"p{number}_b{mb.reading_order}",
                        bbox=bbox_ndl,
                        type=mb.type,
                        text=mb.text.strip(),
                        reading_order=mb.reading_order,
                        source="marker",
                    )
                )
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
            block_key[f"p{number}_u{j}"] = line_idx[id(line)]

    vertical_page = _is_vertical(ndl.lines) if ndl.lines else False
    _reorder_by_ndl(page, block_key, vertical_page)
    if not vertical_page:
        _attach_captions(page)
    page.blocks = _merge_label_pairs(page.blocks)
    return page


# 図表キャプションらしい本文（「図1 …」「表2-3 …」「Fig. 4」）。
_CAPTION_TEXT = re.compile(r"^(?:図|表|Fig\.?|Figure|Table)\s*[0-9０-９A-Za-z]", re.IGNORECASE)


def _attach_captions(page: Page) -> None:
    """キャプションを、同じページで最も近い図/表の直前（上にあるとき）か直後に移す。

    実本（CKD診療ガイド）では、Docling の読み順だと「図4 …」が図の後の2段組本文を
    すべて読み終えた後に出ていた。また本文扱いになっているキャプション（短い・
    句点なし・「図N」で始まる）も CAPTION に直す。
    """
    blocks = sorted(page.blocks, key=lambda b: b.reading_order)
    for b in blocks:
        t = b.text.strip()
        if (
            b.type is BlockType.BODY
            and _CAPTION_TEXT.match(t)
            and len(t) <= 80
            and "。" not in t
        ):
            b.type = BlockType.CAPTION
    targets = [b for b in blocks if b.type in (BlockType.FIGURE, BlockType.TABLE)]
    if not targets:
        return
    for cap in [b for b in blocks if b.type is BlockType.CAPTION]:
        best, best_gap = None, None
        for f in targets:
            # 横方向に重なりがあるものだけ（隣の段の図には付けない）
            if min(cap.bbox.x1, f.bbox.x1) <= max(cap.bbox.x0, f.bbox.x0):
                continue
            if cap.bbox.cy >= f.bbox.cy:
                gap = max(0.0, cap.bbox.y0 - f.bbox.y1)
            else:
                gap = max(0.0, f.bbox.y0 - cap.bbox.y1)
            if best_gap is None or gap < best_gap:
                best, best_gap = f, gap
        # 遠すぎる（ページ高の15%超）なら動かさない
        if best is None or (page.height and best_gap > page.height * 0.15):
            continue
        blocks.remove(cap)
        idx = blocks.index(best)
        blocks.insert(idx + 1 if cap.bbox.cy >= best.bbox.cy else idx, cap)
    for i, b in enumerate(blocks, start=1):
        b.reading_order = i
    page.blocks = blocks


_DIGITS_ONLY = re.compile(r"^[\s0-9０-９IVXivx]+$")


def _title_page_heading(
    number: int, ndl: NDLPage, marker: LayoutPage | None, page_h: float
) -> Block | None:
    """章扉（大きな章番号＋章タイトルだけのページ）を見出し1ブロックにする。

    実本「3秒で心電図を読む本」の章扉は構造エンジンが何も検出せず、NDL 行が本文として
    救済された後、本文整形で前ページ（目次）の末尾に連結されて章タイトルが消えていた。
    条件: 表紙付近（3ページ目まで）ではない／構造エンジンが図・表を検出していない／
    柱・ノンブル域を除いた行が1〜4行で合計40字以内。数字だけの行（章番号）は除く。
    """
    if number <= 3 or not ndl.lines:
        return None
    if marker is not None and any(
        b.type in (BlockType.FIGURE, BlockType.TABLE) for b in marker.blocks
    ):
        return None  # 図表のあるページは対象外（図を落とさないため）
    lines = [
        ln for ln in ndl.lines
        if _rescued_line_type(ln, page_h) is BlockType.BODY and not _DIGITS_ONLY.match(ln.text)
    ]
    if not (1 <= len(lines) <= 4) or sum(len(ln.text) for ln in lines) > 40:
        return None
    ordered = _order_lines(lines)
    return Block(
        id=f"p{number}_t1",
        bbox=_lines_bbox(ordered),
        type=BlockType.HEADING,
        text=" ".join(ln.text.strip() for ln in ordered),
        orientation=_majority_orientation(ordered),
        reading_order=1,
        heading_level=1,
        confidence=_avg_conf(ordered),
        source="ndl-title",
    )


def _lines_bbox(lines: list[NDLLine]) -> BBox:
    return BBox(
        x0=min(ln.bbox.x0 for ln in lines),
        y0=min(ln.bbox.y0 for ln in lines),
        x1=max(ln.bbox.x1 for ln in lines),
        y1=max(ln.bbox.y1 for ln in lines),
    )


def _reorder_by_ndl(page: Page, key: dict[str, int], vertical: bool) -> None:
    """NDL の行順を使って読み順を補正する（reading_order を振り直す）。

    - 縦書きページ: 構造エンジンは横書き前提で段落の順を取り違える（実本「3秒で心電図
      を読む本」の序文で、段落が逆順・ばらばらに出た）。テキストを持つブロックは NDL の
      行順で並べ、図はその右端より左（=縦書きでは後）にある最初の行の直前に置く。
    - 横書きページ: 構造エンジンの順を尊重しつつ、救済行（どのブロックにも入らなかった
      NDL 行）だけを、NDL で直前にある行を含むブロックの直後へ差し込む
      （従来はページ末尾に付けていたため、文の途中の断片が無関係な位置に出ていた）。
    """
    blocks = sorted(page.blocks, key=lambda b: b.reading_order)
    if not key:
        return
    if vertical:
        keyed = [b for b in blocks if b.id in key]
        if len(keyed) < 2:
            return
        def sort_key(b: Block) -> float:
            if b.id in key:
                return float(key[b.id])
            # 図など: 自分より左にある（後に読む）テキストのうち最小の行順の直前。
            later = [key[o.id] for o in keyed if o.bbox.cx < b.bbox.x0]
            return (min(later) - 0.5) if later else float("inf")
        ordered = sorted(blocks, key=sort_key)
    else:
        rescued = [b for b in blocks if b.source == "ndl-rescue" and b.id in key]
        if not rescued:
            return
        ordered = [b for b in blocks if b not in rescued]
        for r in sorted(rescued, key=lambda b: key[b.id]):
            # NDL 順で r より前にある行を持つブロックのうち、最も r に近いもの。
            prev = [b for b in ordered if b.id in key and key[b.id] < key[r.id]]
            if not prev:
                ordered.insert(0, r)
                continue
            anchor = max(prev, key=lambda b: key[b.id])
            ordered.insert(ordered.index(anchor) + 1, r)
    for i, b in enumerate(ordered, start=1):
        b.reading_order = i
    page.blocks = ordered


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


# 見出しらしくない文字列: 句読点で終わる（＝文）／数字・記号だけ（ノンブル等）。
_SENTENCE_TAIL = tuple("。、．，")
_SYMBOLS_ONLY = re.compile(r"^[\s0-9０-９\W_]+$")
# これより長い「見出し」は本文の太字段落の誤検出とみなす。
_MAX_HEADING_CHARS = 60


def demote_false_headings(doc: Document) -> int:
    """構造エンジンが見出しと誤認した本文を BODY に戻す（LLM 不要の規則）。

    実本「3秒で心電図を読む本」で、太字・大きめの本文（「ことでしょう。」「心電図に
    あまり多くを求めない。」）やノンブル「31」「11」が見出しとして出て、目次が
    本文の断片だらけになった。句点で終わる・長すぎる・数字記号だけ、は見出しにしない。
    戻り値: 降格した数。
    """
    n = 0
    for page in doc.pages:
        for b in page.blocks:
            if b.type is not BlockType.HEADING:
                continue
            t = b.text.strip()
            if (
                not t
                or t.endswith(_SENTENCE_TAIL)
                or "。" in t
                or len(t) > _MAX_HEADING_CHARS
                or _SYMBOLS_ONLY.match(t)
            ):
                b.type = BlockType.BODY
                b.heading_level = None
                n += 1
    return n


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
    demote_false_headings(doc)
    assign_heading_levels(doc)
    return doc
