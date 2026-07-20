"""バッチ・オーケストレーション。

1冊(1 PDF) の処理:
    NDLOCR-Lite ───────────┐
                           ├─▶ merge ─▶ IR(json) ─▶ [任意: GUI補正] ─▶ Markdown
    構造エンジン ──────────┘         （engine = docling / mineru / marker）
    (Docling/MinerU/Marker)

複数 PDF の一括処理・進捗表示・処理済みスキップ・リトライを提供する。
NDL / 構造エンジンの実行はユーザー環境（Windows, CPU）で走る前提。この環境では
アダプタのパース〜レンダリングまでを合成データで検証できる。
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import PipelineConfig
from .ir import DocMeta, Document
from .pipeline import docling as docling_mod
from .pipeline import marker as marker_mod
from .pipeline import merge as merge_mod
from .pipeline import mineru as mineru_mod
from .pipeline import ndl as ndl_mod
from .pipeline import structure_llm as structure_mod
from .pipeline.layout import LayoutPage
from .render import render_document


def _engine_adapter(cfg: PipelineConfig):
    """config.engine に応じて (engine_config, run_fn, load_fn) を返す。

    run_fn(input_pdf, outdir, engine_cfg) / load_fn(outdir, json_glob)。
    """
    engine = (cfg.engine or "docling").lower()
    if engine == "marker":
        return cfg.marker, marker_mod.run_marker, marker_mod.load_marker_dir
    if engine == "mineru":
        return cfg.mineru, mineru_mod.run_mineru, mineru_mod.load_mineru_dir
    # 既定: docling
    return cfg.docling, docling_mod.run_docling, docling_mod.load_docling_dir


def render_pdf_pages(pdf_path: str | Path, out_dir: Path, dpi: int = 200) -> list[Path]:
    """PDF 各ページを PNG に描画（図切り出し・GUI表示用）。

    PyMuPDF(fitz) があれば使う。無ければ空リスト（図はプレースホルダになる）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []
    paths: list[Path] = []
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    with fitz.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=mat)
            p = out_dir / f"page_{i:04d}.png"
            pix.save(str(p))
            paths.append(p)
    return paths


def build_ir_for_pdf(
    pdf_path: str | Path,
    cfg: PipelineConfig,
    *,
    run_engines: bool = True,
) -> Document:
    """1 PDF を IR まで持っていく（Markdown 化はしない）。

    run_engines=False のときは NDL/構造エンジンを起動せず、既存の作業ディレクトリの
    JSON を読むだけ（再マージ・GUI補正のやり直しに使える）。

    構造エンジンは cfg.engine（docling / mineru / marker）で選択。
    """
    pdf_path = Path(pdf_path)
    stem = pdf_path.stem
    work = cfg.work_dir / stem
    ndl_out = work / "ndl"
    engine_out = work / cfg.engine
    pages_out = work / "pages"

    engine_cfg, run_engine, load_engine = _engine_adapter(cfg)

    if run_engines:
        if cfg.ndl.enabled:
            ndl_mod.run_ndl(pdf_path, ndl_out, cfg.ndl)
        # NDL の透明PDFがあれば構造エンジン/ページ描画に使う。無ければ元PDF。
        transparent = _first_glob(ndl_out, cfg.ndl.pdf_glob) or pdf_path
        if getattr(engine_cfg, "enabled", True):
            run_engine(transparent, engine_out, engine_cfg)
        render_pdf_pages(transparent, pages_out)

    ndl_pages = ndl_mod.load_ndl_dir(ndl_out, cfg.ndl.json_glob)
    layout_pages: list[LayoutPage] = load_engine(engine_out, engine_cfg.json_glob)

    meta = DocMeta(title=stem, source_pdf=str(pdf_path))
    doc = merge_mod.build_document(ndl_pages, layout_pages, cfg, meta=meta)

    # ページ画像を IR に紐付け（あれば）。
    page_imgs = sorted(pages_out.glob("page_*.png"))
    for i, page in enumerate(doc.pages):
        if i < len(page_imgs):
            page.image_path = str(page_imgs[i])

    # LLM 構造化パス（任意）: 見出し階層の付与＋構成評価。API キーが無ければ no-op。
    review = structure_mod.run_structure_llm(doc, cfg.structure_llm)
    if review:
        doc.meta.structure_review = review

    return doc


def process_pdf(
    pdf_path: str | Path,
    cfg: PipelineConfig,
    *,
    run_engines: bool = True,
) -> Path:
    """1 PDF を Obsidian 用 Markdown まで変換。出力フォルダを返す。"""
    pdf_path = Path(pdf_path)
    doc = build_ir_for_pdf(pdf_path, cfg, run_engines=run_engines)

    # IR を保存（GUI 補正の入力になる）。
    ir_path = cfg.work_dir / pdf_path.stem / "ir.json"
    ir_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(ir_path)

    out_book = cfg.out_dir / pdf_path.stem
    render_document(doc, out_book, cfg.render)
    _write_structure_review(doc, out_book)

    # NDL の透明テキストPDF（OCR結果の目視確認・別ツールでの再利用に使える）を
    # 作業ディレクトリから出力フォルダにもコピーしておく。
    ndl_out = cfg.work_dir / pdf_path.stem / "ndl"
    transparent_pdf = _first_glob(ndl_out, cfg.ndl.pdf_glob)
    if transparent_pdf is not None:
        shutil.copy2(transparent_pdf, out_book / transparent_pdf.name)

    return out_book


def _write_structure_review(doc: Document, out_book: Path) -> None:
    """LLM 構成評価レポートがあれば out_book に書き出す（render 後に呼ぶこと）。

    render_document は out_dir の *.md を掃除するため、必ずレンダリング後に書く。
    """
    if doc.meta.structure_review:
        (out_book / "_structure_review.md").write_text(
            doc.meta.structure_review, encoding="utf-8"
        )


def render_from_ir(ir_path: str | Path, cfg: PipelineConfig, out_name: str | None = None) -> Path:
    """（GUI補正後などの）IR json から Markdown を再生成する。"""
    doc = Document.load(ir_path)
    name = out_name or (doc.meta.title or Path(ir_path).stem)
    out_book = cfg.out_dir / name
    render_document(doc, out_book, cfg.render)
    _write_structure_review(doc, out_book)
    return out_book


def process_folder(
    folder: str | Path,
    cfg: PipelineConfig,
    *,
    pattern: str = "*.pdf",
    skip_existing: bool = True,
    retries: int = 1,
    progress=None,
) -> dict[str, str]:
    """フォルダ内の PDF を一括処理。結果マップ {pdf名: 状態} を返す。"""
    folder = Path(folder)
    pdfs = sorted(folder.glob(pattern))
    results: dict[str, str] = {}
    for pdf in pdfs:
        out_book = cfg.out_dir / pdf.stem
        if skip_existing and (out_book / "00_index.md").exists():
            results[pdf.name] = "skipped"
            if progress:
                progress(pdf.name, "skipped")
            continue
        state = _process_with_retry(pdf, cfg, retries, progress)
        results[pdf.name] = state
    return results


def _process_with_retry(pdf: Path, cfg: PipelineConfig, retries: int, progress) -> str:
    last_err = ""
    for attempt in range(retries + 1):
        try:
            if progress:
                progress(pdf.name, f"processing (try {attempt + 1})")
            process_pdf(pdf, cfg)
            if progress:
                progress(pdf.name, "done")
            return "done"
        except Exception as e:  # noqa: BLE001 - バッチは1冊失敗で止めない
            last_err = str(e)
            if progress:
                progress(pdf.name, f"error: {e}")
            time.sleep(min(8, 2 ** attempt))
    return f"failed: {last_err}"


def _first_glob(root: Path, pattern: str) -> Path | None:
    hits = sorted(Path(root).glob(pattern))
    return hits[0] if hits else None
