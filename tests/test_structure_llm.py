"""LLM 構造化パスの検証（フェイクLLMクライアントで実APIなしに）。

パース・階層適用・誤検出降格・評価レポート・キー無しスキップを合成データで確認する。
実 Gemini 呼び出しはユーザー環境で走る（既存アダプタと同じ流儀）。
"""

from __future__ import annotations

import json

from fullocr.config import StructureLLMConfig
from fullocr.ir import BBox, Block, BlockType, DocMeta, Document, Page
from fullocr.pipeline import structure_llm as sl


class FakeClient:
    """generate() が固定文字列（or 呼び出しごとの応答列）を返すフェイク。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0) if self._responses else ""


def _doc() -> Document:
    """章見出し・節見出し・誤検出見出し・本文を含む1ページ。"""
    page = Page(number=1, width=1000, height=1400)
    page.blocks = [
        Block(id="h1", bbox=BBox(x0=0, y0=0, x1=500, y1=40), type=BlockType.HEADING,
              heading_level=1, text="第1章 総論", reading_order=1),
        Block(id="b1", bbox=BBox(x0=0, y0=50, x1=500, y1=90), type=BlockType.BODY,
              text="本章では総論を述べる。", reading_order=2),
        Block(id="h2", bbox=BBox(x0=0, y0=100, x1=500, y1=140), type=BlockType.HEADING,
              heading_level=1, text="1.1 定義", reading_order=3),
        Block(id="b2", bbox=BBox(x0=0, y0=150, x1=500, y1=190), type=BlockType.BODY,
              text="定義は次のとおり。", reading_order=4),
        Block(id="h3", bbox=BBox(x0=0, y0=200, x1=500, y1=240), type=BlockType.HEADING,
              heading_level=1, text="これは本文が誤って見出し化されたもの", reading_order=5),
    ]
    return Document(meta=DocMeta(title="テスト医学書"), pages=[page])


def test_heading_entries_include_snippet():
    doc = _doc()
    entries = sl._heading_entries(doc, snippet_chars=40)
    assert len(entries) == 3
    assert entries[0]["text"] == "第1章 総論"
    assert "総論を述べる" in entries[0]["snippet"]


def test_parse_heading_response_tolerates_code_fence():
    raw = "```json\n" + json.dumps(
        {"headings": [{"i": 0, "level": 1, "keep": True},
                      {"i": 1, "level": 2, "keep": True},
                      {"i": 2, "level": 1, "keep": False}]}
    ) + "\n```"
    decisions = sl.parse_heading_response(raw, 3)
    assert decisions[0] == (1, True)
    assert decisions[1] == (2, True)
    assert decisions[2] == (1, False)


def test_assign_hierarchy_sets_levels_and_demotes():
    doc = _doc()
    resp = json.dumps({"headings": [
        {"i": 0, "level": 1, "keep": True},
        {"i": 1, "level": 2, "keep": True},
        {"i": 2, "level": 1, "keep": False},  # 誤検出 → 本文へ降格
    ]})
    cfg = StructureLLMConfig(enabled=True, api_key="dummy")
    changed = sl.assign_hierarchy(doc, FakeClient([resp]), cfg)
    assert changed == 3
    headings = sl._heading_blocks(doc)
    # 誤検出が降格され、残る見出しは2つ、レベルは 1 と 2
    assert [b.text for b in headings] == ["第1章 総論", "1.1 定義"]
    assert headings[0].heading_level == 1
    assert headings[1].heading_level == 2
    # 降格ブロックは BODY になり、テキストは保持
    demoted = doc.pages[0].blocks[-1]
    assert demoted.type is BlockType.BODY
    assert "誤って見出し" in demoted.text


def test_run_structure_llm_skips_without_key():
    doc = _doc()
    cfg = StructureLLMConfig(enabled=True, api_key="", api_key_env="___NOPE___")
    # make_client がキー無しで None → 何もしない（例外も出ない）
    assert sl.run_structure_llm(doc, cfg) is None
    # 見出しは元のまま（階層書き換えなし）
    assert len(sl._heading_blocks(doc)) == 3


def test_run_structure_llm_disabled():
    doc = _doc()
    cfg = StructureLLMConfig(enabled=False)
    assert sl.run_structure_llm(doc, cfg) is None


def test_review_prompt_contains_outline_and_title():
    doc = _doc()
    p = sl.build_review_prompt("テスト医学書", sl.outline_text(doc), "巻頭テキスト…")
    assert "テスト医学書" in p
    assert "第1章 総論" in p
