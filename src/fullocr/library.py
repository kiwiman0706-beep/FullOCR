"""変換済み書籍のライブラリ（out/ と work/ を走査して一覧化）。

GUI の「ライブラリ表」の元データ。各書籍について、段階（OCR/レイアウト/生成）が
どこまで済んでいるか、どのAI処理を適用済みか、出力先はどこか、を返す。

AI の適用状況は work/<book>/status.json に永続化する（後付け実行のたびに更新）。
この機能導入前に変換した本には status.json が無いので、ディスク上の痕跡から
ゆるく推測する（例: _structure_review.md があれば structure 済みとみなす）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import PipelineConfig
from .estimate import FEATURES

STATUS_FILE = "status.json"
INDEX_NAME = "00_index.md"


def _has_any(directory: Path, pattern: str) -> bool:
    return directory.is_dir() and any(directory.glob(pattern))


def status_path(cfg: PipelineConfig, name: str) -> Path:
    return cfg.work_dir / name / STATUS_FILE


def load_status(cfg: PipelineConfig, name: str) -> dict:
    p = status_path(cfg, name)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_status(cfg: PipelineConfig, name: str, data: dict) -> None:
    p = status_path(cfg, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_ai(cfg: PipelineConfig, name: str, feature: str, jpy: float | None = None) -> None:
    """あるAI処理を適用済みとして status.json に記録する。"""
    data = load_status(cfg, name)
    ai = data.setdefault("ai", {})
    entry = {"done": True, "at": datetime.now().isoformat(timespec="seconds")}
    if jpy is not None:
        entry["jpy"] = round(jpy, 1)
    ai[feature] = entry
    save_status(cfg, name, data)


def _ai_states(cfg: PipelineConfig, name: str, out_book: Path) -> dict[str, dict]:
    """機能ごとの適用状態を返す（status.json 優先、無ければ痕跡から推測）。"""
    status = load_status(cfg, name).get("ai", {})
    out: dict[str, dict] = {}
    for f in FEATURES:
        if f in status and isinstance(status[f], dict):
            out[f] = {"done": bool(status[f].get("done")), "jpy": status[f].get("jpy"),
                      "at": status[f].get("at")}
            continue
        # フォールバック推測（status.json 導入前の本向け）
        done = False
        if f == "structure" and (out_book / "_structure_review.md").exists():
            done = True
        out[f] = {"done": done, "jpy": None, "at": None}
    return out


def scan_library(cfg: PipelineConfig) -> list[dict]:
    """out/ と work/ にある全書籍を一覧化して返す（名前順）。"""
    names: set[str] = set()
    for base in (cfg.out_dir, cfg.work_dir):
        if base.is_dir():
            names.update(p.name for p in base.iterdir() if p.is_dir())

    books: list[dict] = []
    engine = (cfg.engine or "docling")
    for name in sorted(names):
        work_book = cfg.work_dir / name
        out_book = cfg.out_dir / name
        ndl_done = _has_any(work_book / "ndl", cfg.ndl.json_glob)
        layout_done = _has_any(work_book / engine, "**/*.json")
        render_done = (out_book / INDEX_NAME).exists()
        has_ir = (work_book / "ir.json").exists()
        books.append({
            "name": name,
            "out_dir": str(out_book),
            "has_ir": has_ir,
            "stages": {
                "ocr": "done" if ndl_done else "none",
                "layout": "done" if layout_done else "none",
                "render": "done" if render_done else "none",
            },
            "ai": _ai_states(cfg, name, out_book),
            "source": "library",  # 実行中ジョブは server 側で上書き
        })
    return books
