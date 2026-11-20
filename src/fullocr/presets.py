"""用途別の設定プリセット（実本テストで決めた推奨値の束）。

PipelineConfig は項目が多く、どれを ON にすべきかが分かりにくい。実本テストで
「この用途ならこれ」と確かめた組み合わせを名前付きで持ち、設定ファイルの上から
重ねられるようにする（`fullocr init-config --preset` / `run|batch --preset`）。

プリセットは差分（上書きする項目だけ）で持つ。既定値（PipelineConfig()）自体も
テスト結果に合わせて調整してあるので、既定＝"bookhatch" とほぼ同じ。
各値の根拠は docs/presets.md。
"""

from __future__ import annotations

from typing import Any

from .config import PipelineConfig

# name -> (説明, 上書き差分)
PRESETS: dict[str, tuple[str, dict[str, Any]]] = {
    "bookhatch": (
        ("Theta Book Hatch で読む。原本PDFの隣に同じ名前の .jisui を書き、本棚で原本と1冊に"
         "まとまる。不可視ページアンカーで MD⇄原本PDF を同じページで行き来できる。"
         "印刷目次は残す（AI構造化なしだと生成アウトラインが平坦なため）。"),
        {
            "render": {
                "page_anchors": True,
                "pdf_page_links": False,
                "hide_toc_pages": False,
                "tables_as_images": True,
            },
            "pdf_bookmarks": True,
            "jisui_beside_source": True,
        },
    ),
    "obsidian": (
        "Obsidian で読む。見出しごとに可視の「📄原文 p.N」リンクを付け、印刷目次は %% で隠す。",
        {
            "render": {
                "page_anchors": True,
                "pdf_page_links": True,
                "hide_toc_pages": True,
                "tables_as_images": True,
            },
            "pdf_bookmarks": True,
        },
    ),
    "math": (
        ("数式の多い理工系の本。Docling の数式認識で数式を LaTeX（$$…$$）にする。"
         "NDL OCR は分数・添字を崩すため。変換は約7倍遅い（30ページで約16分）。"),
        {
            "docling": {"enrich_formula": True},
            "render": {"page_anchors": True},
        },
    ),
}


def preset_names() -> list[str]:
    return list(PRESETS)


def describe(name: str) -> str:
    return PRESETS[name][0]


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def apply_preset(cfg: PipelineConfig, name: str) -> PipelineConfig:
    """cfg にプリセットの差分を重ねた新しい PipelineConfig を返す（cfg は変更しない）。"""
    if name not in PRESETS:
        raise KeyError(f"未知のプリセット: {name}（選択肢: {', '.join(PRESETS)}）")
    merged = _deep_merge(cfg.model_dump(mode="python"), PRESETS[name][1])
    return PipelineConfig.model_validate(merged)
