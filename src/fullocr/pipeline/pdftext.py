"""born-digital PDF の埋め込みテキスト層から直接 NDLPage を作る（NDL OCR を省く）。

ScanSnap 等でスキャンした PDF は「全面スキャン画像＋透明OCRテキスト層」で、OCR品質が
まちまちなので従来どおり NDL にかける。一方 born-digital（電子生成）PDF はフォント由来の
正確なテキスト層を持ち bbox も厳密なので、それを NDLPage に変換すれば NDL をスキップして
桁違いに速くできる（178ページで約14分 → 数秒）。

判定は保守的: 「実テキストが十分ある」かつ「ページを覆う全面画像が無い」＝ born-digital。
スキャン本（全面画像あり）は born-digital 扱いしない。PyMuPDF(fitz) が無ければ常に False。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..ir import BBox, Orientation
from .ndl import NDLLine, NDLPage


def available() -> bool:
    try:
        import fitz  # noqa: F401  (PyMuPDF)
    except ImportError:
        return False
    return True


def _fullpage_image_ratio(page) -> float:
    """ページを覆う最大画像の面積比（0..1）。スキャン本は ~1 に近い。"""
    parea = float(page.rect.width) * float(page.rect.height)
    if parea <= 0:
        return 0.0
    best = 0.0
    try:
        for info in page.get_image_info():
            b = info.get("bbox")
            if not b:
                continue
            area = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
            best = max(best, area / parea)
    except Exception:  # noqa: BLE001 - fitz API 差異に強く
        return 0.0
    return best


def is_born_digital(pdf_path: str | Path, *, sample_pages: int = 8,
                    min_chars: int = 200, image_cover: float = 0.6,
                    min_ratio: float = 0.6) -> bool:
    """電子生成PDF（テキスト層が本物）か。スキャン本は False。"""
    if not available():
        return False
    import fitz

    try:
        doc = fitz.open(str(pdf_path))
    except Exception:  # noqa: BLE001
        return False
    try:
        n = doc.page_count
        if n == 0:
            return False
        # 均等サンプリング（先頭の扉・空白ページに引っ張られないように）。
        idxs = sorted({int(i * (n - 1) / max(1, sample_pages - 1)) for i in range(min(sample_pages, n))})
        digital = 0
        checked = 0
        for i in idxs:
            page = doc[i]
            text = page.get_text("text") or ""
            if len(text.strip()) < min_chars:
                continue  # 図版・空白ページは判定材料にしない
            checked += 1
            if _fullpage_image_ratio(page) < image_cover:
                digital += 1
        if checked == 0:
            return False  # 文字が乏しい＝写真集/スキャン等 → NDL に任せる
        return digital / checked >= min_ratio
    finally:
        doc.close()


def _line_orientation(line: dict) -> Orientation:
    if line.get("wmode"):  # 1 = 縦書き
        return Orientation.VERTICAL
    d = line.get("dir")
    if d and abs(d[1]) > abs(d[0]):
        return Orientation.VERTICAL
    return Orientation.HORIZONTAL


def load_text_layer_pages(pdf_path: str | Path) -> list[NDLPage]:
    """各ページの埋め込みテキストを行単位で NDLPage に変換する。"""
    import fitz

    doc = fitz.open(str(pdf_path))
    pages: list[NDLPage] = []
    try:
        for page in doc:
            np = NDLPage(width=float(page.rect.width), height=float(page.rect.height))
            data = page.get_text("dict")
            for block in data.get("blocks", []):
                for line in block.get("lines", []):
                    text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    np.lines.append(NDLLine(
                        text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                        orientation=_line_orientation(line), confidence=1.0,
                    ))
            pages.append(np)
    finally:
        doc.close()
    return pages


# ---- --no-engines 再実行用に NDLPage 列をキャッシュする（独自JSON） ----

def save_pages(pages: list[NDLPage], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = [{"width": p.width, "height": p.height,
            "lines": [{"text": ln.text, "bbox": [ln.bbox.x0, ln.bbox.y0, ln.bbox.x1, ln.bbox.y1],
                       "orientation": ln.orientation.value, "confidence": ln.confidence}
                      for ln in p.lines]}
           for p in pages]
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


def load_pages_json(path: str | Path) -> list[NDLPage]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pages: list[NDLPage] = []
    for p in data:
        np = NDLPage(width=p["width"], height=p["height"])
        for ln in p["lines"]:
            b = ln["bbox"]
            np.lines.append(NDLLine(
                text=ln["text"], bbox=BBox(x0=b[0], y0=b[1], x1=b[2], y1=b[3]),
                orientation=Orientation(ln["orientation"]), confidence=ln.get("confidence"),
            ))
        pages.append(np)
    return pages
