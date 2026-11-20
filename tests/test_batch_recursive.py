"""フォルダ一括の再帰処理と、サブフォルダ間の同名衝突回避（prereq #1）の検証。

実OCRは呼ばず process_pdf をフェイクに差し替え、どの book_name で out/work が作られるかを確かめる。
"""

from __future__ import annotations

from pathlib import Path

from fullocr import batch
from fullocr.batch import _unique_book_names
from fullocr.config import PipelineConfig


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4\n")


def test_unique_names_keep_stem_when_no_collision(tmp_path):
    a = tmp_path / "subA" / "本X.pdf"
    b = tmp_path / "subB" / "本Y.pdf"
    _touch(a); _touch(b)
    names = _unique_book_names([a, b], tmp_path)
    assert names[a] == "本X"
    assert names[b] == "本Y"


def test_unique_names_disambiguate_collisions_by_relative_path(tmp_path):
    a = tmp_path / "subA" / "総論.pdf"
    b = tmp_path / "subB" / "総論.pdf"
    _touch(a); _touch(b)
    names = _unique_book_names([a, b], tmp_path)
    assert names[a] != names[b]               # 衝突しない
    assert names[a] == "subA_総論"
    assert names[b] == "subB_総論"


def test_unique_names_counter_fallback(tmp_path):
    # 相対パス前置でも同名になる人工ケース（同じ親名）→ 連番で解消
    a = tmp_path / "x" / "本.pdf"
    b = tmp_path / "x_本.pdf"      # safe_book_name("x_本") == "x_本" と衝突しうる
    _touch(a); _touch(b)
    names = _unique_book_names([a, b], tmp_path)
    assert len(set(names.values())) == 2      # 必ず一意


def test_process_folder_recursive_uses_unique_book_names(tmp_path, monkeypatch):
    books = tmp_path / "books"
    _touch(books / "A" / "総論.pdf")
    _touch(books / "B" / "総論.pdf")
    _touch(books / "単独.pdf")
    cfg = PipelineConfig()
    cfg.out_dir = tmp_path / "out"
    cfg.work_dir = tmp_path / "work"

    seen: list[str] = []

    def fake_process_pdf(pdf, cfg, *, run_engines=True, book_name=None, on_step=None, on_output=None):
        seen.append(book_name)
        out_book = cfg.out_dir / book_name
        out_book.mkdir(parents=True, exist_ok=True)
        (out_book / "00_index.md").write_text("# x", encoding="utf-8")
        return out_book

    monkeypatch.setattr(batch, "process_pdf", fake_process_pdf)
    results = batch.process_folder(books, cfg, recursive=True, retries=0)

    assert set(seen) == {"A_総論", "B_総論", "単独"}      # 衝突せず一意
    # 結果キーは相対パス（別フォルダの同名本を区別できる）
    assert "A/総論.pdf" in results and "B/総論.pdf" in results
    # それぞれ別フォルダに出力されている
    assert (cfg.out_dir / "A_総論" / "00_index.md").exists()
    assert (cfg.out_dir / "B_総論" / "00_index.md").exists()


def test_process_folder_non_recursive_ignores_subfolders(tmp_path, monkeypatch):
    books = tmp_path / "books"
    _touch(books / "top.pdf")
    _touch(books / "sub" / "deep.pdf")
    cfg = PipelineConfig()
    cfg.out_dir = tmp_path / "out"
    cfg.work_dir = tmp_path / "work"

    seen: list[str] = []
    monkeypatch.setattr(batch, "process_pdf",
                        lambda pdf, cfg, **k: seen.append(k.get("book_name")) or (cfg.out_dir / "x"))
    batch.process_folder(books, cfg, recursive=False, retries=0, skip_existing=False)
    assert seen == ["top"]        # サブフォルダは辿らない


def _write_jisui(path, generator="fullocr 0.14.0"):
    import json
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/x-jisui", compress_type=zipfile.ZIP_STORED)
        z.writestr("theta-book.json", json.dumps({"format": "jisui-1", "generator": generator}))


def test_resume_skips_when_our_jisui_beside_source(tmp_path, monkeypatch):
    books = tmp_path / "books"
    _touch(books / "本.pdf")
    _write_jisui(books / "本.jisui")           # FullOCR が書いた既存 .jisui
    cfg = PipelineConfig()
    cfg.out_dir = tmp_path / "out"
    cfg.work_dir = tmp_path / "work"
    cfg.jisui_beside_source = True

    def boom(*a, **k):
        raise AssertionError("既に .jisui があるなら process_pdf は呼ばれない")

    monkeypatch.setattr(batch, "process_pdf", boom)
    results = batch.process_folder(books, cfg, recursive=True)
    assert results["本.pdf"] == "skipped"


def test_resume_does_not_skip_foreign_jisui(tmp_path, monkeypatch):
    books = tmp_path / "books"
    _touch(books / "本.pdf")
    _write_jisui(books / "本.jisui", generator="someone-else")   # 他人の .jisui
    cfg = PipelineConfig()
    cfg.out_dir = tmp_path / "out"
    cfg.work_dir = tmp_path / "work"
    cfg.jisui_beside_source = True

    called: list[str] = []
    monkeypatch.setattr(batch, "process_pdf",
                        lambda pdf, cfg, **k: called.append(k.get("book_name")) or (cfg.out_dir / "x"))
    batch.process_folder(books, cfg, recursive=True)
    assert called == ["本"]        # 他人の .jisui ではスキップしない
