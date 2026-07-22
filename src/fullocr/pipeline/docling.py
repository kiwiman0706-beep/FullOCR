"""Docling アダプタ（推奨エンジン）。

役割: Docling の出力（DoclingDocument を dict/JSON にエクスポートしたもの）から
      レイアウト構造を取り出し、共通の LayoutPage にする。

NDL 協業の考え方:
  - NDL-Lite が作る「透明テキスト付きPDF」を Docling に渡し、Docling 側の OCR を
    切る（テキストモード）と、Docling は NDL の埋め込みテキストをそのまま採り、
    レイアウト・表・見出し階層・読み順だけを担当する。
  - さらに厳密にやるなら、merge.py の座標マージで NDL テキストを主とし、Docling は
    構造のみ供給する使い方も可能（このアダプタはどちらでも使える）。

DoclingDocument JSON（要点）:
  {
    "pages": { "1": {"size": {"width": W, "height": H}, "page_no": 1}, ... },
    "body":  {"children": [ {"$ref": "#/texts/0"}, {"$ref": "#/pictures/0"}, ... ]},
    "texts":    [ {"self_ref":"#/texts/0","label":"section_header","level":1,
                   "prov":[{"page_no":1,"bbox":{"l":..,"t":..,"r":..,"b":..,
                            "coord_origin":"BOTTOMLEFT"}}], "text":"..."}, ... ],
    "pictures": [ {"label":"picture","prov":[{...}]}, ... ],
    "tables":   [ {"label":"table","prov":[{...}]}, ... ],
    "groups":   [ {"children":[...]}, ... ],
  }
読み順は body(→groups) のツリー順で決まるので、それを辿って平坦化する。
座標は coord_origin により BOTTOMLEFT / TOPLEFT があるため、ページ高で上下反転して
IR の左上原点(px) に正規化する。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

from ..config import DoclingConfig
from ..ir import BBox, BlockType
from .layout import LayoutBlock, LayoutPage
from .proc import run_streaming
from .shellutil import quote_path

# Docling は同じ本の中でも図表キャプションを "caption" と "section_header" に
# 一貫性なく振り分けることが実データで確認された（フォントの太さ等の見た目次第らしい）。
# "section_header" 扱いでも「図1」「表1」等で始まるテキストはキャプションとして
# 扱い直す（見出しの過検出・過剰分割を防ぐ）。
_CAPTION_LIKE = re.compile(r"^[図表]\s*\d")

# Docling の label -> IR BlockType。
_LABEL_MAP: dict[str, BlockType] = {
    "title": BlockType.HEADING,
    "section_header": BlockType.HEADING,
    "text": BlockType.BODY,
    "paragraph": BlockType.BODY,
    "formula": BlockType.BODY,
    "code": BlockType.BODY,
    "caption": BlockType.CAPTION,
    "picture": BlockType.FIGURE,
    "image": BlockType.FIGURE,
    "table": BlockType.TABLE,
    "list_item": BlockType.LIST,
    "page_header": BlockType.PAGE_HEADER,
    "page_footer": BlockType.PAGE_FOOTER,
    "footnote": BlockType.FOOTNOTE,
}


def _resolve_ref(ref: str) -> tuple[str, int] | None:
    """"#/texts/3" -> ("texts", 3)。"""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def _iter_body_items(data: dict) -> list[dict]:
    """body(→groups) を読み順に辿り、texts/pictures/tables の実体を順に返す。"""
    ordered: list[dict] = []
    seen: set[tuple[str, int]] = set()

    def walk(node: dict, depth: int = 0) -> None:
        if depth > 64:  # 参照ループ保険
            return
        for child in node.get("children", []) or []:
            ref = child.get("$ref") if isinstance(child, dict) else None
            resolved = _resolve_ref(ref) if ref else None
            if resolved is None:
                # インラインで子を持つ形もありうる
                if isinstance(child, dict) and child.get("children"):
                    walk(child, depth + 1)
                continue
            kind, idx = resolved
            if kind == "groups":
                groups = data.get("groups", [])
                if 0 <= idx < len(groups):
                    walk(groups[idx], depth + 1)
                continue
            arr = data.get(kind, [])
            if 0 <= idx < len(arr) and (kind, idx) not in seen:
                seen.add((kind, idx))
                ordered.append(arr[idx])

    body = data.get("body")
    if isinstance(body, dict):
        walk(body)

    # body が無い/空なら texts+tables+pictures をそのまま採用（読み順は近似）。
    if not ordered:
        for kind in ("texts", "tables", "pictures"):
            ordered.extend(data.get(kind, []) or [])
    return ordered


def _page_sizes(data: dict) -> dict[int, tuple[float, float]]:
    sizes: dict[int, tuple[float, float]] = {}
    pages = data.get("pages", {})
    if isinstance(pages, dict):
        for k, v in pages.items():
            try:
                pno = int(v.get("page_no", k))
            except (ValueError, TypeError):
                continue
            size = v.get("size", {}) or {}
            sizes[pno] = (float(size.get("width", 0) or 0), float(size.get("height", 0) or 0))
    return sizes


def _to_bbox(prov_bbox: dict, page_h: float) -> BBox | None:
    """Docling の bbox(l,t,r,b + coord_origin) を IR の左上原点(px) BBox に。"""
    if not isinstance(prov_bbox, dict):
        return None
    try:
        left = float(prov_bbox["l"])
        right = float(prov_bbox["r"])
        top = float(prov_bbox["t"])
        bottom = float(prov_bbox["b"])
    except (KeyError, TypeError, ValueError):
        return None
    origin = str(prov_bbox.get("coord_origin", "TOPLEFT")).upper()
    if origin == "BOTTOMLEFT" and page_h > 0:
        # 下原点: t,b は下からの距離(t>b)。上原点へ反転。
        y0 = page_h - max(top, bottom)
        y1 = page_h - min(top, bottom)
    else:
        y0 = min(top, bottom)
        y1 = max(top, bottom)
    return BBox(x0=min(left, right), y0=y0, x1=max(left, right), y1=y1)


def _extract_table_cells(item: dict, page_h: float) -> list[dict] | None:
    """Docling の table item から行・列のセルグリッドを取り出す。

    DoclingDocument の表は `data.table_cells` に、各セルの bbox と
    start/end_row/col_offset_idx（＝行番号・列番号の範囲）を持っている。
    テキストはここでは使わず（NDL 側を merge.py で bbox マッチさせる）、
    位置とグリッド座標だけを渡す。
    """
    cells = ((item.get("data") or {}).get("table_cells")) or []
    out: list[dict] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        bbox = _to_bbox(cell.get("bbox", {}), page_h)
        if bbox is None:
            continue
        try:
            row = int(cell.get("start_row_offset_idx", 0))
            col = int(cell.get("start_col_offset_idx", 0))
            row_span = max(1, int(cell.get("end_row_offset_idx", row + 1)) - row)
            col_span = max(1, int(cell.get("end_col_offset_idx", col + 1)) - col)
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "bbox": bbox,
                "row": row,
                "col": col,
                "row_span": row_span,
                "col_span": col_span,
                "is_header": bool(cell.get("column_header") or cell.get("row_header")),
            }
        )
    return out or None


def _label_to_type(label: str) -> BlockType:
    return _LABEL_MAP.get(str(label).lower(), BlockType.OTHER)


def parse_docling_json(data) -> list[LayoutPage]:
    """DoclingDocument(dict/JSON) を LayoutPage のリストへ。"""
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        return []

    sizes = _page_sizes(data)
    items = _iter_body_items(data)

    # Docling は本文アイテムが1つも無いページ（白紙・扉ページ等）でも "pages" に
    # サイズ情報だけは持っている。先にこれで全ページ分を埋めておかないと、そういう
    # ページがリストから欠番になり、merge.py 側でページ番号ではなく位置で NDL と
    # 対応付けているために以降全ページがズレて合成される（実本フル変換で発覚した
    # バグ）。
    pages: dict[int, LayoutPage] = {pno: LayoutPage(width=w, height=h) for pno, (w, h) in sizes.items()}
    order_counter: dict[int, int] = {pno: 0 for pno in pages}

    for item in items:
        if not isinstance(item, dict):
            continue
        provs = item.get("prov") or []
        if not provs:
            continue
        prov = provs[0]
        try:
            pno = int(prov.get("page_no", 1))
        except (ValueError, TypeError):
            pno = 1
        page_w, page_h = sizes.get(pno, (0.0, 0.0))
        bbox = _to_bbox(prov.get("bbox", {}), page_h)
        if bbox is None:
            continue

        btype = _label_to_type(item.get("label", ""))
        text = str(item.get("text", "") or "")
        if btype is BlockType.HEADING and _CAPTION_LIKE.match(text):
            btype = BlockType.CAPTION
        # section_header の level を見出しレベルへ。
        level = item.get("level")
        heading_level = int(level) if (btype is BlockType.HEADING and level) else None
        if btype is BlockType.HEADING and item.get("label") == "title":
            heading_level = heading_level or 1

        table_cells = _extract_table_cells(item, page_h) if btype is BlockType.TABLE else None

        page = pages.get(pno)
        if page is None:
            page = LayoutPage(width=page_w, height=page_h)
            pages[pno] = page
            order_counter[pno] = 0
        order_counter[pno] += 1
        page.blocks.append(
            LayoutBlock(
                bbox=bbox,
                type=btype,
                reading_order=order_counter[pno],
                heading_level=heading_level,
                text=text,
                table_cells=table_cells,
            )
        )

    if not pages:
        return []
    return [pages.get(k, LayoutPage()) for k in range(1, max(pages) + 1)]


def load_docling_dir(outdir: str | Path, json_glob: str = "**/*.json") -> list[LayoutPage]:
    outdir = Path(outdir)
    for f in sorted(outdir.glob(json_glob)):
        try:
            parsed = parse_docling_json(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            continue
        if parsed:
            return parsed  # 1ドキュメント=1JSONを想定（最初に見つかった有効JSON）
    return []


def run_docling(input_pdf: str | Path, outdir: str | Path, cfg: DoclingConfig, on_output=None) -> Path:
    """Docling を実行して JSON を outdir に生成する。

    通常（開発環境・pip install 済み）は `cfg.command` の CLI を subprocess で叩く。
    PyInstaller で固めた exe の中では、"docling" という別コマンドは存在しない
    （フリーズされているのは fullocr 自身のエントリポイントだけで、docling 自体は
    Python ライブラリとして同梱されているだけ）。`sys.frozen` で凍結実行を検知し、
    その場合だけ Docling の Python API を直接呼ぶ（同じプロセス内・同じ依存を再利用
    するので、Docling 用に別exeを二重にビルドせずに済む）。

    `on_output(line)` を渡すと進捗を1行ずつ流す。CLI 経路はその標準出力、
    in-process 経路は docling の logging を拾って渡す（GUI のライブコンソール用）。
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        run_docling_inprocess(input_pdf, outdir, on_output)
        return outdir
    cmd = cfg.command.format(input=quote_path(str(input_pdf)), outdir=quote_path(str(outdir)))
    returncode, tail = run_streaming(cmd, on_output)
    if returncode != 0:
        raise RuntimeError(
            f"Docling failed (exit {returncode}) for {input_pdf}.\n"
            f"command: {cmd}\n--- output (tail) ---\n{tail}"
        )
    return outdir


class _LogForwarder(logging.Handler):
    """logging レコードを on_output(line) へ横流しするハンドラ（in-process用）。"""

    def __init__(self, on_output) -> None:
        super().__init__()
        self._on_output = on_output

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._on_output(record.getMessage())
        except Exception:  # noqa: BLE001 - ログ転送で本処理を止めない
            pass


def run_docling_inprocess(input_pdf: str | Path, outdir: str | Path, on_output=None) -> Path:
    """Docling の Python API を直接呼んで JSON を書き出す（exe 同梱用）。

    `docling convert {input} --to json --output {outdir} --no-ocr` と等価な処理を
    サブプロセスを介さず行う。`on_output` を渡すと、docling のロガー出力（ページ処理
    などの進捗）を拾って流す（凍結アプリには標準出力が無いので logging で拾う）。
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    input_pdf = Path(input_pdf)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    handler: _LogForwarder | None = None
    dlog = logging.getLogger("docling")
    prev_level = dlog.level
    if on_output is not None:
        handler = _LogForwarder(on_output)
        dlog.addHandler(handler)
        dlog.setLevel(logging.INFO)  # ページ処理ログを拾えるレベルに
    try:
        pipeline_options = PdfPipelineOptions(do_ocr=False)
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        result = converter.convert(str(input_pdf))
        out_path = outdir / f"{input_pdf.stem}.json"
        result.document.save_as_json(out_path)
        return out_path
    finally:
        if handler is not None:
            dlog.removeHandler(handler)
            dlog.setLevel(prev_level)
