"""中間表現 (Intermediate Representation, IR)。

FullOCR の全工程はこの IR を介してつながる。座標付きのブロック列として文書を
表現するため、後付けの GUI 補正（読み順の並べ替え・種別変更・テキスト修正）を
最終 Markdown ではなくこの段階で行える。

    NDL-Lite の高精度テキスト  +  Marker のレイアウト種別  =  IR

JSON にシリアライズできる（GUI ↔ パイプラインの受け渡しフォーマット）。
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    """ブロックの意味的種別。主に Marker のレイアウト解析結果から決まる。"""

    HEADING = "heading"      # 見出し（level で階層を持つ）
    BODY = "body"            # 本文段落
    CAPTION = "caption"      # 図表キャプション
    FIGURE = "figure"        # 挿絵・図（画像として切り出す）
    TABLE = "table"          # 表
    LIST = "list"            # 箇条書き
    PAGE_HEADER = "page_header"  # ページ上部のヘッダ（柱・ノンブル等）
    PAGE_FOOTER = "page_footer"  # ページ下部のフッタ
    FOOTNOTE = "footnote"    # 脚注
    OTHER = "other"


class Orientation(str, Enum):
    HORIZONTAL = "horizontal"  # 横書き
    VERTICAL = "vertical"      # 縦書き


class BBox(BaseModel):
    """ピクセル座標のバウンディングボックス（左上原点）。"""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def intersection_area(self, other: "BBox") -> float:
        ix0 = max(self.x0, other.x0)
        iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1)
        iy1 = min(self.y1, other.y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        return (ix1 - ix0) * (iy1 - iy0)

    @classmethod
    def from_points(cls, points: list[list[float]]) -> "BBox":
        """4点（またはポリゴン）の座標リストから外接矩形を作る。

        NDL-Lite / Marker はいずれも角の座標配列で領域を表すため、それを
        軸並行の矩形へ正規化する。
        """
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
        return cls(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


class Block(BaseModel):
    """文書中の1ブロック（1行〜1段落、または1つの図）。"""

    id: str
    bbox: BBox
    type: BlockType = BlockType.BODY
    text: str = ""
    orientation: Orientation = Orientation.HORIZONTAL
    reading_order: int = 0
    heading_level: int | None = None  # type == HEADING のとき 1..6
    confidence: float | None = None
    image_path: str | None = None     # type == FIGURE のとき切り出し画像の相対パス
    source: str = ""                  # 由来メモ（"ndl", "marker", "merged" 等）


class Page(BaseModel):
    """1ページ。ブロックは reading_order 昇順で並ぶことを期待する。"""

    number: int                       # 1始まりのページ番号
    width: float                      # ページ画像の幅(px)
    height: float                     # ページ画像の高さ(px)
    image_path: str | None = None     # ページ画像（図切り出し・GUI表示の元）
    blocks: list[Block] = Field(default_factory=list)

    def ordered_blocks(self) -> list[Block]:
        return sorted(self.blocks, key=lambda b: b.reading_order)


class DocMeta(BaseModel):
    title: str | None = None
    author: str | None = None
    source_pdf: str | None = None
    # 同梱した透明テキストPDFのファイル名（見出し→PDFページリンクの張り先）。
    pdf_name: str | None = None
    # 書誌メタ（biblio.py がネットから取得。既定は空）。front-matter に出力される。
    publisher: str | None = None
    published: str | None = None
    isbn: str | None = None
    page_count: int | None = None
    categories: list[str] = Field(default_factory=list)
    cover_url: str | None = None
    # LLM 構造化パスが生成した構成評価レポート（あれば _structure_review.md に出力）。
    structure_review: str | None = None
    # 印刷された「目次」ページのページ番号（tocdetect が推定）。非表示化・LLMへの
    # 目次画像送付・見出しとの突き合わせの起点になる。
    toc_pages: list[int] = Field(default_factory=list)


class Document(BaseModel):
    """1冊分の IR。"""

    meta: DocMeta = Field(default_factory=DocMeta)
    pages: list[Page] = Field(default_factory=list)

    # ---- JSON I/O -------------------------------------------------------
    def to_json(self, *, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Document":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        return json.loads(self.to_json())
