"""ライブラリ走査・コスト概算・後付けAI実行の検証。"""

from __future__ import annotations

import json

from fullocr import batch, estimate, library
from fullocr.config import PipelineConfig
from fullocr.ir import BBox, Block, BlockType, DocMeta, Document, Page


def _doc_with_content() -> Document:
    page = Page(number=1, width=1000, height=1400)
    page.blocks = [
        Block(id="h1", bbox=BBox(x0=0, y0=0, x1=500, y1=40), type=BlockType.HEADING,
              heading_level=1, text="第1章 総論", reading_order=1),
        Block(id="b1", bbox=BBox(x0=0, y0=50, x1=500, y1=90), type=BlockType.BODY,
              text="本章では総論を述べる。" * 20, reading_order=2),
        Block(id="f1", bbox=BBox(x0=0, y0=100, x1=500, y1=400), type=BlockType.FIGURE,
              reading_order=3),
    ]
    return Document(meta=DocMeta(title="テスト書"), pages=[page])


def test_estimate_all_features_and_rewrite_is_priciest():
    cfg = PipelineConfig()
    est = estimate.estimate_all(_doc_with_content(), cfg)
    assert set(est) == {"structure", "rewrite", "figure_alt", "anomaly", "digest"}
    # リライトは出力トークンが本文まるごと分かかるので、構造付けより高い
    assert est["rewrite"]["jpy"] >= est["structure"]["jpy"]
    # 図が1枚あるので figure_alt は適用可能・費用>0
    assert est["figure_alt"]["applicable"] is True
    assert est["figure_alt"]["jpy"] > 0


def test_estimate_figure_alt_not_applicable_without_figures():
    doc = _doc_with_content()
    doc.pages[0].blocks = [b for b in doc.pages[0].blocks if b.type is not BlockType.FIGURE]
    est = estimate.estimate_feature(doc, "figure_alt", PipelineConfig())
    assert est["applicable"] is False


def test_scan_library_reports_stages(tmp_path):
    cfg = PipelineConfig(work_dir=tmp_path / "work", out_dir=tmp_path / "out")
    name = "サンプル本"
    (cfg.work_dir / name / "ndl").mkdir(parents=True)
    (cfg.work_dir / name / "ndl" / "page.json").write_text("{}", encoding="utf-8")
    (cfg.work_dir / name / "docling").mkdir(parents=True)
    (cfg.work_dir / name / "docling" / "doc.json").write_text("{}", encoding="utf-8")
    (cfg.work_dir / name / "ir.json").write_text("{}", encoding="utf-8")
    (cfg.out_dir / name).mkdir(parents=True)
    (cfg.out_dir / name / "00_index.md").write_text("# x", encoding="utf-8")

    books = library.scan_library(cfg)
    assert len(books) == 1
    b = books[0]
    assert b["name"] == name
    assert b["stages"] == {"ocr": "done", "layout": "done", "render": "done"}
    assert b["has_ir"] is True
    assert all(v["done"] is False for v in b["ai"].values())


def test_record_ai_persists_and_shows_done(tmp_path):
    cfg = PipelineConfig(work_dir=tmp_path / "work", out_dir=tmp_path / "out")
    name = "本B"
    (cfg.work_dir / name).mkdir(parents=True)
    library.record_ai(cfg, name, "structure", jpy=3.0)
    data = json.loads((cfg.work_dir / name / "status.json").read_text(encoding="utf-8"))
    assert data["ai"]["structure"]["done"] is True
    assert data["ai"]["structure"]["jpy"] == 3.0
    # scan で done として出る
    (cfg.out_dir / name).mkdir(parents=True)
    b = library.scan_library(cfg)[0]
    assert b["ai"]["structure"]["done"] is True


class _FakeClient:
    def generate(self, prompt, *, system=None):
        # 見出し1件を H1 のまま保持する応答
        return json.dumps({"headings": [{"i": 0, "level": 1, "keep": True}]})


def test_postprocess_runs_structure_and_records(tmp_path, monkeypatch):
    import fullocr.llm as llm_mod

    cfg = PipelineConfig(work_dir=tmp_path / "work", out_dir=tmp_path / "out")
    cfg.structure_llm.api_key = "dummy"
    name = "テスト書"
    (cfg.work_dir / name).mkdir(parents=True)
    _doc_with_content().save(cfg.work_dir / name / "ir.json")

    monkeypatch.setattr(llm_mod, "make_client", lambda scfg: _FakeClient())
    result = batch.postprocess_book(cfg, name, ["structure"])
    assert result["applied"] == ["structure"]
    # 出力が生成され、status に structure=done が記録される
    assert (cfg.out_dir / name / "00_index.md").exists()
    assert library.load_status(cfg, name)["ai"]["structure"]["done"] is True
