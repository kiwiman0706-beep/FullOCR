"""巻末索引→見出し語抽出（Stage 1）の検証。"""

from __future__ import annotations

from fullocr import indexterms
from fullocr.config import StructureLLMConfig
from fullocr.ir import BBox, Block, BlockType, DocMeta, Document, Page
from fullocr.pipeline import tocdetect


def _page(number, lines):
    p = Page(number=number, width=1000, height=1400)
    for i, (text, typ) in enumerate(lines):
        p.blocks.append(Block(id=f"p{number}b{i}", bbox=BBox(x0=0, y0=i * 30, x1=500, y1=i * 30 + 25),
                              type=typ, text=text, reading_order=i))
    return p


def test_detect_index_pages_at_back():
    body = [_page(i, [("本文です。", BlockType.BODY)]) for i in range(1, 9)]
    idx = _page(9, [
        ("索引", BlockType.HEADING),
        ("アシュネル眼球現象 46", BlockType.BODY),
        ("意識障害 173", BlockType.BODY),
        ("ワレンベルグ症候群 88, 92", BlockType.BODY),
    ])
    doc = Document(pages=body + [idx])
    assert tocdetect.detect_index_pages(doc) == [9]


def test_accept_filter_drops_latin_and_symbols():
    assert indexterms._accept("意識障害") is True       # 全漢字でも採用
    assert indexterms._accept("1型糖尿病") is True       # 数字＋漢字
    assert indexterms._accept("アシュネル眼球現象") is True
    assert indexterms._accept("KRAS遺伝子") is False     # ラテン文字
    assert indexterms._accept("A00-B99") is False        # 日本語なし
    assert indexterms._accept("57, 61") is False         # 数字記号のみ


class _FakeClient:
    def __init__(self, response):
        self._r = response
        self.prompts = []

    def generate(self, prompt, *, system=None):
        self.prompts.append(prompt)
        return self._r


def test_extract_index_terms_filters_and_dedups():
    doc = Document(meta=DocMeta(title="t", index_pages=[1]), pages=[
        _page(1, [("索引", BlockType.HEADING), ("意識障害 173", BlockType.BODY)]),
    ])
    # LLM 応答（表記のみ・ラテン語や重複を混ぜる）
    resp = "意識障害\nアシュネル眼球現象\nKRAS遺伝子\n意識障害\n- 1型糖尿病\n"
    client = _FakeClient(resp)
    terms = indexterms.extract_index_terms(doc, client, StructureLLMConfig(api_key="x"))
    assert terms == ["意識障害", "アシュネル眼球現象", "1型糖尿病"]  # ラテン除去・重複1回・行頭記号除去
    assert "索引" in client.prompts[0]  # 索引ページ本文がプロンプトに入る


def test_extract_none_without_index_pages():
    doc = Document(meta=DocMeta(title="t", index_pages=[]), pages=[_page(1, [("x", BlockType.BODY)])])
    assert indexterms.extract_index_terms(doc, _FakeClient(""), StructureLLMConfig()) == []
