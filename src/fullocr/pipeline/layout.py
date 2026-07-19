"""レイアウト解析エンジン共通の表現。

Marker / Docling / MinerU いずれのアダプタも、この LayoutPage / LayoutBlock を
出力する。マージ (merge.py) はこの共通型だけに依存し、どのエンジンでも同じように
NDL テキストと合成できる（＝エンジン差し替え可能）。

テキストの真実は NDL 側にあるため、LayoutBlock.text は補助（見出しレベル推定や
図キャプションの当たり付け）に使う程度で、最終テキストにはしない設計。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ir import BBox, BlockType


@dataclass
class LayoutBlock:
    bbox: BBox
    type: BlockType
    reading_order: int
    heading_level: int | None = None
    text: str = ""
    # type == TABLE のとき、セルの行・列グリッド（あれば）。
    # 各要素: {"bbox": BBox, "row": int, "col": int, "row_span": int,
    #          "col_span": int, "is_header": bool}
    table_cells: list[dict] | None = None


@dataclass
class LayoutPage:
    width: float = 0.0
    height: float = 0.0
    blocks: list[LayoutBlock] = field(default_factory=list)
