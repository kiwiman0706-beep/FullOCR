"""AI要約・マインドマップ・一問一答（digest）の検証。ネット/LLMには出ない（fake client）。

要点: 要約文はLLMだが、**引用ページはIR由来**（捏造しない）こと。リンクが
原文PDF(#page=N)と生成MD見出しの両方向で付くこと。
"""

from __future__ import annotations

import json

import pytest

from fullocr import digest as dg
from fullocr.config import DigestConfig
from fullocr.ir import BBox, Block, BlockType, DocMeta, Document, Page


def _page(number, blocks):
    p = Page(number=number, width=1000, height=1400)
    for i, (text, typ, lv) in enumerate(blocks):
        p.blocks.append(Block(id=f"p{number}b{i}", bbox=BBox(x0=0, y0=i * 40, x1=500, y1=i * 40 + 30),
                              type=typ, text=text, heading_level=lv, reading_order=i))
    return p


def _doc():
    return Document(meta=DocMeta(title="神経内科", pdf_name="神経内科_text.pdf"), pages=[
        _page(9, [("第1章 意識障害", BlockType.HEADING, 1), ("意識障害ではABCと血糖を評価する。", BlockType.BODY, None)]),
        _page(10, [("本文つづき。GCSは3軸で評価する。", BlockType.BODY, None)]),
        _page(11, [("第2章 脳卒中", BlockType.HEADING, 1), ("t-PAには適応と禁忌がある。", BlockType.BODY, None)]),
    ])


class _FakeClient:
    """節ごとに固定の JSON を返す。"""

    def __init__(self):
        self.calls = 0

    def generate(self, prompt, *, system=None):
        self.calls += 1
        return json.dumps({
            "summary": [f"要点{self.calls}A", f"要点{self.calls}B"],
            "qa": [{"q": f"問{self.calls}", "a": f"答{self.calls}"}],
            "map": {"label": f"主題{self.calls}", "children": ["子1", "子2"]},
        }, ensure_ascii=False)


def test_segments_by_headings_with_page_ranges():
    secs = dg.segment(_doc(), DigestConfig())
    heads = [(s.heading, s.page_start, s.page_end) for s in secs]
    assert ("第1章 意識障害", 9, 10) in heads
    assert ("第2章 脳卒中", 11, 11) in heads


def test_build_digest_and_source_links_are_grounded():
    doc = _doc()
    client = _FakeClient()
    digest = dg.build_digest(doc, client, DigestConfig())
    md = dg.render_digest_md(digest, doc, DigestConfig())

    # 原文PDFリンクは IR のページ（第1章=p9）を指す（LLMには言わせていない）
    assert "[[神経内科_text.pdf#page=9|📄p.9]]" in md
    assert "[[神経内科_text.pdf#page=11|" in md
    # 生成MD見出しへのwikilinkも付く
    assert "#第1章 意識障害|§第1章 意識障害]]" in md
    # 3種すべて見出しが出る
    assert "## 📖 構造化要約" in md
    assert "```mermaid" in md and "mindmap" in md
    assert "## 🎴 一問一答" in md
    # LLMは節数ぶん呼ばれる
    assert client.calls == len([s for s in dg.segment(doc, DigestConfig())])


def test_kinds_filter_limits_sections():
    doc = _doc()
    digest = dg.build_digest(doc, _FakeClient(), DigestConfig(kinds=["summary"]))
    md = dg.render_digest_md(digest, doc, DigestConfig(kinds=["summary"]))
    assert "## 📖 構造化要約" in md
    assert "```mermaid" not in md
    assert "🎴" not in md


def test_links_can_be_pdf_only():
    doc = _doc()
    cfg = DigestConfig(link_md=False)
    digest = dg.build_digest(doc, _FakeClient(), cfg)
    md = dg.render_digest_md(digest, doc, cfg)
    assert "📄p.9" in md
    assert "§第1章" not in md  # MD見出しリンクは出さない


def test_window_fallback_when_no_headings():
    doc = Document(meta=DocMeta(title="t"), pages=[
        _page(1, [("本文のみ1。", BlockType.BODY, None)]),
        _page(2, [("本文のみ2。", BlockType.BODY, None)]),
    ])
    secs = dg.segment(doc, DigestConfig(segment_pages=1))
    assert len(secs) == 2
    assert secs[0].heading.startswith("pp.")


def test_bad_json_is_tolerated():
    class Bad:
        def generate(self, prompt, *, system=None):
            return "これはJSONではありません"
    doc = _doc()
    digest = dg.build_digest(doc, Bad(), DigestConfig())
    # 落ちず、要約は空でもセクションは返る
    assert len(digest.sections) >= 2
    assert all(s.summary == [] for s in digest.sections)


def test_epub_is_self_contained_with_internal_anchors(tmp_path):
    pytest.importorskip("ebooklib")
    import zipfile

    doc = _doc()
    digest = dg.build_digest(doc, _FakeClient(), DigestConfig())
    out = dg.build_epub(digest, doc, tmp_path / "d.epub", DigestConfig())
    assert out.exists()
    z = zipfile.ZipFile(out)
    names = z.namelist()
    dig = z.read(next(n for n in names if n.endswith("digest.xhtml"))).decode()
    txt = z.read(next(n for n in names if n.endswith("text.xhtml"))).decode()
    # ダイジェスト→本文ページへ EPUB 内ジャンプ、本文側にアンカーがある（自己完結）
    assert "text.xhtml#pdf-9" in dig
    assert 'id="pdf-9"' in txt
