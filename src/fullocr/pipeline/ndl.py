"""NDLOCR-Lite アダプタ。

役割: NDLOCR-Lite の JSON（座標付きテキスト行）を読み、正規化した行リストにする。
テキストの真実は NDL 側を採用する（縦書き認識・日本語精度が Marker より高いため）。

NDLOCR-Lite の出力仕様（要点）:
  - GPU 不要・CPU のみ（ONNX Runtime）、Windows/macOS/Linux 対応
  - 出力形式: TXT / JSON / 透明テキスト付きPDF
  - JSON: 1行ごとに boundingBox(座標)・text・confidence・書字方向 を持つ

注意: NDL-Lite のバージョンによって JSON のキー名や入れ子構造が異なりうる。
この parser は複数の一般的な形に対して寛容に動くよう作ってあり、実際の
出力に合わせて `_KEY_ALIASES` を調整するのが最初の作業（README のフェーズ0）。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import NDLConfig
from ..ir import BBox, Orientation
from .shellutil import quote_path

# JSON キー名のゆらぎを吸収する別名テーブル。
_TEXT_KEYS = ("text", "content", "value", "recognition")
_BOX_KEYS = ("boundingBox", "bbox", "box", "points", "polygon")
_CONF_KEYS = ("confidence", "conf", "score")
# 実機の NDLOCR-Lite (v1.x) は行の向きを "isVertical": "true"/"false" で持つ。
_DIR_KEYS = ("direction", "orientation", "writing_mode", "writingMode", "isVertical")
_CONTENTS_KEYS = ("contents", "lines", "words", "cells", "results", "items")
# 実機は "img_width"/"img_height"（--sourcepdf の pages[]・--sourceimg/--sourcedir の imginfo）を使う。
_WIDTH_KEYS = ("width", "imageWidth", "w", "img_width")
_HEIGHT_KEYS = ("height", "imageHeight", "h", "img_height")


@dataclass
class NDLLine:
    text: str
    bbox: BBox
    orientation: Orientation = Orientation.HORIZONTAL
    confidence: float | None = None


@dataclass
class NDLPage:
    width: float = 0.0
    height: float = 0.0
    lines: list[NDLLine] = field(default_factory=list)


def _first(d: dict, keys: tuple[str, ...], default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _to_bbox(raw) -> BBox | None:
    """座標表現（4点/多点、または [x0,y0,x1,y1]）を BBox に。"""
    if raw is None:
        return None
    # [x0, y0, x1, y1]
    if isinstance(raw, (list, tuple)) and len(raw) == 4 and all(
        isinstance(v, (int, float)) for v in raw
    ):
        x0, y0, x1, y1 = (float(v) for v in raw)
        return BBox(x0=min(x0, x1), y0=min(y0, y1), x1=max(x0, x1), y1=max(y0, y1))
    # [[x,y],[x,y],...]
    if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], (list, tuple)):
        return BBox.from_points([list(p) for p in raw])
    # flat [x,y,x,y,x,y,...]
    if isinstance(raw, (list, tuple)) and len(raw) >= 6 and len(raw) % 2 == 0:
        pts = [[raw[i], raw[i + 1]] for i in range(0, len(raw), 2)]
        return BBox.from_points(pts)
    return None


def _to_orientation(raw) -> Orientation:
    if raw is None:
        return Orientation.HORIZONTAL
    s = str(raw).lower()
    if s in ("vertical", "tb", "ttb", "縦", "縦書き", "1", "true"):
        return Orientation.VERTICAL
    return Orientation.HORIZONTAL


def _parse_line(item: dict) -> NDLLine | None:
    text = _first(item, _TEXT_KEYS, "")
    bbox = _to_bbox(_first(item, _BOX_KEYS))
    if bbox is None:
        return None
    return NDLLine(
        text=str(text or "").strip(),
        bbox=bbox,
        orientation=_to_orientation(_first(item, _DIR_KEYS)),
        confidence=_first(item, _CONF_KEYS),
    )


def _parse_page(obj: dict) -> NDLPage:
    page = NDLPage(
        width=float(_first(obj, _WIDTH_KEYS, 0) or 0),
        height=float(_first(obj, _HEIGHT_KEYS, 0) or 0),
    )
    contents = _first(obj, _CONTENTS_KEYS, [])
    for item in contents or []:
        if isinstance(item, dict):
            line = _parse_line(item)
            if line and line.text:
                page.lines.append(line)
    return page


def parse_ndl_json(data) -> list[NDLPage]:
    """NDL-Lite JSON（dict / list / JSON文字列）を NDLPage のリストに変換。

    受理する形:
      0. 実機の NDLOCR-Lite (v1.x, --sourcepdf/--sourceimg/--sourcedir) 出力:
         {"contents": [ [line, ...], [line, ...], ... ], "pages": [ {img_width,img_height,...}, ... ]}
         （--sourcepdf。複数ページなら "pages" がページ毎メタ情報）
         {"contents": [ [line, ...] ], "imginfo": {img_width,img_height,...}}
         （--sourceimg / --sourcedir。1ファイル=1ページ）
         "contents" はページ毎の"行配列のリスト"であり、寸法は別キーに置かれている点が
         下記の想定形式と異なる（README フェーズ0で確認済みの実際の形）。
      1. {"pages": [ {page}, ... ]}             （page 自体が width/height/contents を持つ想定形）
      2. [ {page}, {page}, ... ]              （ページ配列）
      3. {page}                                （単一ページ）
      4. [ {line}, {line}, ... ]               （行のフラット配列＝1ページ扱い）
    """
    if isinstance(data, str):
        data = json.loads(data)

    if isinstance(data, dict):
        contents = data.get("contents")
        if isinstance(contents, list) and (not contents or isinstance(contents[0], list)):
            page_infos = data.get("pages")
            if page_infos is None:
                page_infos = [data.get("imginfo") or {}]
            pages: list[NDLPage] = []
            for i, lines_raw in enumerate(contents):
                info = page_infos[i] if i < len(page_infos) else {}
                page = NDLPage(
                    width=float(_first(info, _WIDTH_KEYS, 0) or 0),
                    height=float(_first(info, _HEIGHT_KEYS, 0) or 0),
                )
                for item in lines_raw or []:
                    if isinstance(item, dict):
                        line = _parse_line(item)
                        if line and line.text:
                            page.lines.append(line)
                pages.append(page)
            return pages

    if isinstance(data, dict) and "pages" in data:
        return [_parse_page(p) for p in data["pages"]]

    if isinstance(data, dict):
        # ページ辞書か、行だけ入った辞書か。contents があればページ。
        if _first(data, _CONTENTS_KEYS) is not None:
            return [_parse_page(data)]
        return []

    if isinstance(data, list):
        if not data:
            return []
        # ページ配列か、行のフラット配列か判定。
        looks_like_page = isinstance(data[0], dict) and (
            _first(data[0], _CONTENTS_KEYS) is not None
            or _first(data[0], _WIDTH_KEYS) is not None
        )
        if looks_like_page:
            return [_parse_page(p) for p in data]
        # 行のフラット配列 → 1ページ
        page = NDLPage()
        for item in data:
            if isinstance(item, dict):
                line = _parse_line(item)
                if line and line.text:
                    page.lines.append(line)
        # ページサイズ不明なら行の外接から推定
        if page.lines:
            page.width = max(ln.bbox.x1 for ln in page.lines)
            page.height = max(ln.bbox.y1 for ln in page.lines)
        return [page]

    return []


def load_ndl_dir(outdir: str | Path, json_glob: str = "**/*.json") -> list[NDLPage]:
    """NDL の出力ディレクトリから JSON を集めてページ列にする。

    複数ファイル（ページ毎JSON）とページ配列JSONの両方に対応。ファイル名で整列。
    """
    outdir = Path(outdir)
    files = sorted(outdir.glob(json_glob))
    pages: list[NDLPage] = []
    for f in files:
        try:
            parsed = parse_ndl_json(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            continue
        pages.extend(parsed)
    return pages


def run_ndl(input_pdf: str | Path, outdir: str | Path, cfg: NDLConfig) -> Path:
    """NDLOCR-Lite CLI を実行して JSON / 透明PDF を outdir に生成する。

    実バイナリはこの環境には無いので、実際の変換時（ユーザーの Windows）に走る。
    戻り値は outdir。
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    # 自炊本のファイル名は空白・記号を含みがち（実データで確認済み）。shell=True の
    # コマンド文字列に埋め込む前に quote してトークン分割崩れを防ぐ。
    cmd = cfg.command.format(input=quote_path(str(input_pdf)), outdir=quote_path(str(outdir)))
    # 出力を握り、失敗時は末尾を例外に載せる。GUI(コンソール無し)ではプロセスの
    # stderr がどこにも出ないため、これをしないと NDL 失敗理由がログに残らない。
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-2000:]
        raise RuntimeError(
            f"NDLOCR-Lite failed (exit {proc.returncode}) for {input_pdf}.\n"
            f"command: {cmd}\n--- output (tail) ---\n{tail}"
        )
    return outdir
