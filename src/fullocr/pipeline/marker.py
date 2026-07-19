"""Marker アダプタ。

役割: Marker の JSON 出力（ブロックツリー）を読み、レイアウト構造だけを取り出す。
      - ブロック種別（見出し / 本文 / 図 / キャプション / 表 …）
      - 図の領域（切り出し用）
      - 読み順（Marker は children を読み順で並べる）

テキストは NDL を優先するため、Marker のテキストは基本使わない（見出しレベルの
推定などに補助的に使う程度）。

Marker JSON の構造（要点）:
  ルート -> children=[Pageブロック] -> 各 Page の children=[ブロック...] -> ネスト
  各ブロックは block_type, polygon(4点) または bbox, html, children を持つ。
座標は「Marker がレンダリングした画像ピクセル or PDFポイント」で、NDL の座標系と
一致しない可能性がある。マージ側で各ページ幅高さによる正規化(0..1)で吸収する。
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

from ..config import MarkerConfig
from ..ir import BBox, BlockType
from .layout import LayoutBlock, LayoutPage

# 後方互換のための別名（旧コード/テストが MarkerBlock/MarkerPage を参照する）。
MarkerBlock = LayoutBlock
MarkerPage = LayoutPage

# Marker の block_type -> IR BlockType の対応。
_TYPE_MAP: dict[str, BlockType] = {
    "Title": BlockType.HEADING,
    "SectionHeader": BlockType.HEADING,
    "Text": BlockType.BODY,
    "TextInlineMath": BlockType.BODY,
    "Paragraph": BlockType.BODY,
    "Caption": BlockType.CAPTION,
    "Figure": BlockType.FIGURE,
    "Picture": BlockType.FIGURE,
    "Image": BlockType.FIGURE,
    "Table": BlockType.TABLE,
    "TableGroup": BlockType.TABLE,
    "ListItem": BlockType.LIST,
    "ListGroup": BlockType.LIST,
    "PageHeader": BlockType.PAGE_HEADER,
    "PageFooter": BlockType.PAGE_FOOTER,
    "Footnote": BlockType.FOOTNOTE,
}

# これ以上たどらず1ブロックとして扱う種別（葉として採用）。
_LEAF_TYPES = set(_TYPE_MAP) - {"TableGroup", "ListGroup"}

_HTAG_RE = re.compile(r"<h([1-6])", re.IGNORECASE)


def _block_bbox(node: dict) -> BBox | None:
    if node.get("bbox") is not None:
        b = node["bbox"]
        if isinstance(b, (list, tuple)) and len(b) == 4:
            x0, y0, x1, y1 = (float(v) for v in b)
            return BBox(x0=min(x0, x1), y0=min(y0, y1), x1=max(x0, x1), y1=max(y0, y1))
    poly = node.get("polygon")
    if isinstance(poly, (list, tuple)) and poly and isinstance(poly[0], (list, tuple)):
        return BBox.from_points([list(p) for p in poly])
    return None


def _base_type(block_type: str) -> str:
    """"Text", "SectionHeader/…" のような値から素の型名を取り出す。"""
    if not block_type:
        return ""
    return str(block_type).split("/")[0].split(".")[-1].strip()


def _heading_level_from_html(html: str | None) -> int | None:
    if not html:
        return None
    m = _HTAG_RE.search(html)
    return int(m.group(1)) if m else None


def _walk(node: dict, counter: list[int], out: list[MarkerBlock]) -> None:
    btype = _base_type(node.get("block_type", ""))
    ir_type = _TYPE_MAP.get(btype)
    children = node.get("children") or []

    if btype in _LEAF_TYPES and ir_type is not None:
        bbox = _block_bbox(node)
        if bbox is not None:
            counter[0] += 1
            out.append(
                MarkerBlock(
                    bbox=bbox,
                    type=ir_type,
                    reading_order=counter[0],
                    heading_level=_heading_level_from_html(node.get("html")),
                    text=_strip_html(node.get("html", "")),
                )
            )
        return  # 葉として採用したら中はたどらない

    for child in children:
        if isinstance(child, dict):
            _walk(child, counter, out)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return _TAG_RE.sub("", html or "").strip()


def parse_marker_json(data) -> list[MarkerPage]:
    """Marker JSON を MarkerPage のリストへ。"""
    if isinstance(data, str):
        data = json.loads(data)

    # ルートが Page 配列を children に持つ / いきなり Page 配列 / 単一 dict を許容
    root_children: list
    if isinstance(data, dict) and data.get("children"):
        # ルートが Document か Page か
        if _base_type(data.get("block_type", "")) == "Page":
            root_children = [data]
        else:
            root_children = data["children"]
    elif isinstance(data, list):
        root_children = data
    elif isinstance(data, dict):
        root_children = [data]
    else:
        return []

    pages: list[MarkerPage] = []
    for pnode in root_children:
        if not isinstance(pnode, dict):
            continue
        pbbox = _block_bbox(pnode)
        page = MarkerPage(
            width=pbbox.width if pbbox else 0.0,
            height=pbbox.height if pbbox else 0.0,
        )
        counter = [0]
        for child in pnode.get("children") or []:
            if isinstance(child, dict):
                _walk(child, counter, page.blocks)
        pages.append(page)
    return pages


def load_marker_dir(outdir: str | Path, json_glob: str = "**/*.json") -> list[MarkerPage]:
    outdir = Path(outdir)
    files = sorted(outdir.glob(json_glob))
    pages: list[MarkerPage] = []
    for f in files:
        try:
            pages.extend(parse_marker_json(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError):
            continue
    return pages


def run_marker(input_pdf: str | Path, outdir: str | Path, cfg: MarkerConfig) -> Path:
    """Marker CLI を実行して JSON を outdir に生成する（実行はユーザー環境）。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = cfg.command.format(input=shlex.quote(str(input_pdf)), outdir=shlex.quote(str(outdir)))
    subprocess.run(cmd, shell=True, check=True)
    return outdir
