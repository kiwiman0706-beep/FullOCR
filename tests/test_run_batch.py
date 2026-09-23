"""「お任せ」一括変換 run_batch の要約・ログ・レポート出力を検証。

process_pdf をフェイクに差し替え、実OCRなしで成功/失敗の集計を確かめる。
狙い: 進捗はログファイルへ、要約と失敗一覧だけが戻る（トークン節約設計）。
"""

from __future__ import annotations

import json

from fullocr import batch
from fullocr.config import PipelineConfig


def _make_pdfs(folder, names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(b"%PDF-1.4\n")


def test_run_batch_summary_and_report(tmp_path, monkeypatch):
    books = tmp_path / "books"
    _make_pdfs(books, ["ok_a.pdf", "ng_b.pdf", "ok_c.pdf"])
    cfg = PipelineConfig()
    cfg.out_dir = tmp_path / "out"
    cfg.work_dir = tmp_path / "work"

    def fake_process_pdf(pdf, cfg, run_engines=True):
        if pdf.stem.startswith("ng"):
            raise RuntimeError("壊れたPDFです")
        return cfg.out_dir / pdf.stem

    monkeypatch.setattr(batch, "process_pdf", fake_process_pdf)

    rep = batch.run_batch(books, cfg, retries=0)

    assert rep["total"] == 3
    assert sorted(rep["done"]) == ["ok_a.pdf", "ok_c.pdf"]
    assert rep["skipped"] == []
    assert len(rep["failed"]) == 1
    fail = rep["failed"][0]
    assert fail["name"] == "ng_b.pdf"
    assert "壊れたPDF" in fail["error"]

    # レポート JSON がディスクに残る（失敗本だけ後追いできる）
    report_path = cfg.out_dir / "batch_report.json"
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["failed"][0]["name"] == "ng_b.pdf"

    # 進捗はログファイルへ流れている
    log_text = (cfg.out_dir / "batch.log").read_text(encoding="utf-8")
    assert "ng_b.pdf" in log_text
    assert "batch end" in log_text


def test_run_batch_skips_existing(tmp_path, monkeypatch):
    books = tmp_path / "books"
    _make_pdfs(books, ["done_x.pdf"])
    cfg = PipelineConfig()
    cfg.out_dir = tmp_path / "out"
    # 既に変換済みの体裁を作る
    (cfg.out_dir / "done_x").mkdir(parents=True)
    (cfg.out_dir / "done_x" / "00_index.md").write_text("# done", encoding="utf-8")

    def fail_if_called(*a, **k):  # スキップされれば呼ばれない
        raise AssertionError("process_pdf should not run for skipped book")

    monkeypatch.setattr(batch, "process_pdf", fail_if_called)

    rep = batch.run_batch(books, cfg)
    assert rep["skipped"] == ["done_x.pdf"]
    assert rep["done"] == []
    assert rep["failed"] == []
