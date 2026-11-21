"""費用概算の全書籍集計（fullocr estimate）の検証。LLMは呼ばない。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from fullocr.cli import app
from fullocr.ir import BBox, Block, BlockType, DocMeta, Document, Page

runner = CliRunner()


def _book(work, name, bodychars=2000):
    (work / name).mkdir(parents=True)
    blocks = [
        Block(id="h", bbox=BBox(x0=0, y0=0, x1=400, y1=30), type=BlockType.HEADING,
              text="第1章", heading_level=1, reading_order=0),
        Block(id="b", bbox=BBox(x0=0, y0=40, x1=400, y1=400), type=BlockType.BODY,
              text="あ" * bodychars, reading_order=1),
    ]
    doc = Document(meta=DocMeta(title=name), pages=[Page(number=1, width=400, height=560, blocks=blocks)])
    doc.save(work / name / "ir.json")


def test_estimate_aggregates_across_books(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    work = tmp_path / "work"
    _book(work, "A", bodychars=2000)
    _book(work, "B", bodychars=4000)

    r = runner.invoke(app, ["estimate", "work", "--feature", "structure,digest",
                            "--json", "-c", "nonexistent.json"])
    assert r.exit_code == 0, r.output
    rep = json.loads(r.output)
    assert rep["books"] == 2
    assert rep["feature_totals_jpy"]["digest"] > 0
    assert rep["grand_total_jpy"] == round(
        rep["feature_totals_jpy"]["structure"] + rep["feature_totals_jpy"]["digest"], 1)
    assert rep["feature_applicable_books"]["digest"] == 2
    assert {row["book"] for row in rep["per_book"]} == {"A", "B"}
    assert (tmp_path / "out" / "estimate_report.json").exists()
