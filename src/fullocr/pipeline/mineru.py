"""MinerU アダプタ（数式・表が濃い専門書向けの代替エンジン）。

役割: MinerU の中間出力 `*_middle.json` からレイアウト構造を取り出し、共通の
      LayoutPage にする。MinerU は CJK レイアウト検出と数式(LaTeX)・表が強い。

NDL 協業の考え方:
  - MinerU は自前 OCR(PaddleOCR系) も持つが、日本語書籍のテキスト精度は NDL が上。
    よって MinerU からは構造(bbox/種別/読み順)を採り、テキストは merge.py で NDL を
    主にする（座標マージ）。

MinerU middle.json（要点）:
  {
    "pdf_info": [
      {
        "page_idx": 0,
        "page_size": [W, H],
        "preproc_blocks": [
          {"type": "title", "bbox": [x0,y0,x1,y1], "lines": [...]},
          {"type": "text",  "bbox": [...], "lines": [...]},
          {"type": "image", "bbox": [...], "blocks": [ {"type":"image_body"...},
                                                       {"type":"image_caption"...} ]},
          {"type": "table", "bbox": [...], "blocks": [...]},
          {"type": "interline_equation", "bbox": [...]},
        ],
      }, ...
    ]
  }
座標は画像 px（左上原点）想定。type を IR BlockType に対応付ける。
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from ..config import MinerUConfig
from ..ir import BBox, BlockType
from .layout import LayoutBlock, LayoutPage

_TYPE_MAP: dict[str, BlockType] = {
    "title": BlockType.HEADING,
    "text": BlockType.BODY,
    "plain_text": BlockType.BODY,
    "interline_equation": BlockType.BODY,
    "equation": BlockType.BODY,
    "image": BlockType.FIGURE,
    "image_body": BlockType.FIGURE,
    "figure": BlockType.FIGURE,
    "image_caption": BlockType.CAPTION,
    "table_caption": BlockType.CAPTION,
    "table": BlockType.TABLE,
    "table_body": BlockType.TABLE,
    "list": BlockType.LIST,
    "index": BlockType.OTHER,
    "discarded": BlockType.OTHER,
}


def _to_bbox(raw) -> BBox | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    return BBox(x0=min(x0, x1), y0=min(y0, y1), x1=max(x0, x1), y1=max(y0, y1))


def _block_text(block: dict) -> str:
    """preproc_block の lines/spans からテキストを拾う（補助用）。"""
    parts: list[str] = []
    for line in block.get("lines", []) or []:
        for span in line.get("spans", []) or []:
            t = span.get("content") or span.get("text")
            if t:
                parts.append(str(t))
    return "".join(parts)


def _heading_level(block: dict) -> int | None:
    lvl = block.get("text_level") or block.get("level")
    try:
        return int(lvl) if lvl else None
    except (ValueError, TypeError):
        return None


def _walk_block(block: dict, page: LayoutPage, counter: list[int]) -> None:
    btype_raw = str(block.get("type", "")).lower()
    ir_type = _TYPE_MAP.get(btype_raw)

    # image / table は入れ子(blocks)を持ち、本体とキャプションが分かれる。
    nested = block.get("blocks")
    if nested:
        for sub in nested:
            if isinstance(sub, dict):
                _walk_block(sub, page, counter)
        return

    if ir_type is None:
        return
    bbox = _to_bbox(block.get("bbox"))
    if bbox is None:
        return
    counter[0] += 1
    page.blocks.append(
        LayoutBlock(
            bbox=bbox,
            type=ir_type,
            reading_order=counter[0],
            heading_level=_heading_level(block) if ir_type is BlockType.HEADING else None,
            text=_block_text(block),
        )
    )


def parse_mineru_json(data) -> list[LayoutPage]:
    """MinerU middle.json(dict/JSON) を LayoutPage のリストへ。"""
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        return []

    pdf_info = data.get("pdf_info")
    if not isinstance(pdf_info, list):
        return []

    pages: list[LayoutPage] = []
    for pinfo in pdf_info:
        if not isinstance(pinfo, dict):
            continue
        size = pinfo.get("page_size") or [0, 0]
        page = LayoutPage(
            width=float(size[0]) if len(size) > 0 else 0.0,
            height=float(size[1]) if len(size) > 1 else 0.0,
        )
        counter = [0]
        for block in pinfo.get("preproc_blocks", []) or pinfo.get("para_blocks", []) or []:
            if isinstance(block, dict):
                _walk_block(block, page, counter)
        pages.append(page)
    return pages


def load_mineru_dir(outdir: str | Path, json_glob: str = "**/*middle.json") -> list[LayoutPage]:
    outdir = Path(outdir)
    candidates = sorted(outdir.glob(json_glob)) or sorted(outdir.glob("**/*.json"))
    for f in candidates:
        try:
            parsed = parse_mineru_json(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            continue
        if parsed:
            return parsed
    return []


def run_mineru(input_pdf: str | Path, outdir: str | Path, cfg: MinerUConfig) -> Path:
    """MinerU を実行して middle.json 等を outdir に生成する（実行はユーザー環境）。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = cfg.command.format(input=shlex.quote(str(input_pdf)), outdir=shlex.quote(str(outdir)))
    subprocess.run(cmd, shell=True, check=True)
    return outdir
