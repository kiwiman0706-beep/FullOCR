"""AI処理の消費トークン・料金の概算（GUIの「概算 ¥○」表示用）。

保存済みの IR（fullocr.ir.Document）から、機能ごとに送受信トークン量を見積もり、
モデル単価（config.CostConfig）を掛けて概算 ¥ を出す。**あくまで概算**：日本語の
トークン換算には幅があり、実際の課金と数割ずれることがある。

機能キー:
    structure    見出し階層付け（見出し＋直後スニペット＋巻頭テキストを送る／出力は小）
    anomaly      本文の異常チェック（境界前後 or 全文を送る／出力は小）
    rewrite      本文リライト（本文全体を送り、ほぼ同量を書き戻す＝出力が高い）
    figure_alt   挿絵の代替テキスト（図の枚数×画像トークン＋短い説明）
"""

from __future__ import annotations

from .config import CostConfig, StructureLLMConfig
from .ir import BlockType, Document

FEATURES = ("structure", "rewrite", "figure_alt", "anomaly")

# 図1枚あたりの概算トークン（vision入力）と説明文の出力。粗い目安。
_IMAGE_TOKENS_IN = 1100
_IMAGE_TOKENS_OUT = 45


def _tok(chars: float, cost: CostConfig) -> int:
    return max(0, int(chars / max(0.1, cost.chars_per_token)))


def _body_chars(doc: Document) -> int:
    return sum(
        len(b.text)
        for page in doc.pages
        for b in page.blocks
        if b.type is BlockType.BODY and b.text.strip()
    )


def _all_text_chars(doc: Document) -> int:
    return sum(
        len(b.text)
        for page in doc.pages
        for b in page.blocks
        if b.type in (BlockType.BODY, BlockType.HEADING) and b.text.strip()
    )


def _heading_chars(doc: Document) -> int:
    return sum(
        len(b.text)
        for page in doc.pages
        for b in page.blocks
        if b.type is BlockType.HEADING and b.text.strip()
    )


def _heading_count(doc: Document) -> int:
    return sum(
        1
        for page in doc.pages
        for b in page.blocks
        if b.type is BlockType.HEADING and b.text.strip()
    )


def _figure_count(doc: Document, only_missing_alt: bool = True) -> int:
    n = 0
    for page in doc.pages:
        for b in page.blocks:
            if b.type is not BlockType.FIGURE:
                continue
            if only_missing_alt and b.text.strip():
                continue
            n += 1
    return n


def _tokens_for(doc: Document, feature: str, scfg: StructureLLMConfig, cost: CostConfig) -> tuple[int, int]:
    """(tokens_in, tokens_out) を機能ごとに見積もる。"""
    if feature == "structure":
        # 巻頭参照＋見出し＋スニペット（≒見出し数×snippet）を入力、出力は見出し数×小。
        ref = min(scfg.reference_head_chars, _all_text_chars(doc))
        hc = _heading_count(doc)
        in_chars = ref + _heading_chars(doc) + hc * scfg.snippet_chars
        tin = _tok(in_chars, cost) + 400  # 指示文ぶん
        tout = hc * 12 + 50
        return tin, tout
    if feature == "rewrite":
        body = _body_chars(doc)
        tin = _tok(body, cost) + 200
        tout = _tok(body, cost)  # ほぼ入力と同量を書き戻す＝ここが高い
        return tin, tout
    if feature == "figure_alt":
        n = _figure_count(doc, only_missing_alt=True)
        return n * _IMAGE_TOKENS_IN, n * _IMAGE_TOKENS_OUT
    if feature == "anomaly":
        if scfg.anomaly_full_text:
            in_chars = _all_text_chars(doc)
        else:
            # ページ境界前後だけ（1境界あたり ~160 文字）
            in_chars = max(0, len(doc.pages) - 1) * 160
        return _tok(in_chars, cost) + 300, 400
    return 0, 0


def estimate_feature(doc: Document, feature: str, cfg) -> dict:
    """1機能の概算を返す: {tokens_in, tokens_out, usd, jpy, applicable}。

    cfg は PipelineConfig（cost / structure_llm を参照）。figure_alt は対象図が
    無ければ applicable=False。
    """
    cost: CostConfig = cfg.cost
    scfg: StructureLLMConfig = cfg.structure_llm
    tin, tout = _tokens_for(doc, feature, scfg, cost)
    usd = tin / 1_000_000 * cost.price_in_per_mtok + tout / 1_000_000 * cost.price_out_per_mtok
    jpy = usd * cost.usd_jpy
    applicable = True
    if feature == "figure_alt" and _figure_count(doc) == 0:
        applicable = False
    return {
        "feature": feature,
        "tokens_in": tin,
        "tokens_out": tout,
        "usd": round(usd, 4),
        "jpy": round(jpy, 1),
        "applicable": applicable,
    }


def estimate_all(doc: Document, cfg) -> dict[str, dict]:
    """全機能の概算をまとめて返す（{feature: {...}}）。"""
    return {f: estimate_feature(doc, f, cfg) for f in FEATURES}
