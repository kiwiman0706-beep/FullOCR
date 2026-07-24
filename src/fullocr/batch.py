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

import re
import shutil
import time
from pathlib import Path

from .config import PipelineConfig
from .ir import BlockType, DocMeta, Document
from .pipeline import docling as docling_mod
from .pipeline import marker as marker_mod
from .pipeline import merge as merge_mod
from .pipeline import mineru as mineru_mod
from .pipeline import ndl as ndl_mod
from .pipeline import structure_llm as structure_mod
from .pipeline import textclean as textclean_mod
from .pipeline import tocdetect as tocdetect_mod
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


_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_book_name(stem: str) -> str:
    """PDF名から work/out のフォルダ名を作る（ファイルシステム安全化）。

    Windows はディレクトリ名の末尾スペース/ドットを扱えず、mkdir と参照でズレて
    `WinError 3 パスが見つかりません` になる（自炊ファイル名は末尾に空白を含みがち）。
    禁止文字を _ に、末尾の空白・ドットを除去する。IR の title には元名を使う。
    """
    s = _UNSAFE_NAME.sub("_", stem).rstrip(" .").strip()
    return s or "book"


def _noop_step(percent: float, label: str) -> None:
    """進捗コールバック未指定時のデフォルト（何もしない）。"""


def build_ir_for_pdf(
    pdf_path: str | Path,
    cfg: PipelineConfig,
    *,
    run_engines: bool = True,
    on_step=None,
    on_output=None,
) -> Document:
    """1 PDF を IR まで持っていく（Markdown 化はしない）。

    run_engines=False のときは NDL/構造エンジンを起動せず、既存の作業ディレクトリの
    JSON を読むだけ（再マージ・GUI補正のやり直しに使える）。

    構造エンジンは cfg.engine（docling / mineru / marker）で選択。

    on_step(percent, label) を渡すと各段階で進捗を報告する（GUI のバー・ログ用）。
    段階は単発の外部プロセス（OCR/レイアウト解析）が長丁場なので、細かい％では
    なく「いま何をしているか」を主に伝える粒度。
    """
    step = on_step or _noop_step
    pdf_path = Path(pdf_path)
    stem = safe_book_name(pdf_path.stem)   # フォルダ名は安全化（末尾空白等）
    title = pdf_path.stem.strip()          # 表示用の書名は元のまま
    work = cfg.work_dir / stem
    ndl_out = work / "ndl"
    engine_out = work / cfg.engine
    pages_out = work / "pages"

    engine_cfg, run_engine, load_engine = _engine_adapter(cfg)

    if run_engines:
        if cfg.ndl.enabled:
            step(3, "NDLOCR-Lite（日本語OCR）を実行中…（時間がかかります）")
            ndl_mod.run_ndl(pdf_path, ndl_out, cfg.ndl, on_output=on_output)
        # NDL の透明PDFがあれば構造エンジン/ページ描画に使う。無ければ元PDF。
        transparent = _first_glob(ndl_out, cfg.ndl.pdf_glob) or pdf_path
        if getattr(engine_cfg, "enabled", True):
            step(35, f"レイアウト解析（{cfg.engine}）を実行中…")
            run_engine(transparent, engine_out, engine_cfg, on_output=on_output)
        step(58, "ページ画像を描画中…")
        render_pdf_pages(transparent, pages_out, dpi=cfg.page_render_dpi)

    step(66, "OCR結果を読み込み中…")
    ndl_pages = ndl_mod.load_ndl_dir(ndl_out, cfg.ndl.json_glob)
    layout_pages: list[LayoutPage] = load_engine(engine_out, engine_cfg.json_glob)

    step(70, "テキストとレイアウトを統合中…")
    meta = DocMeta(title=title, source_pdf=str(pdf_path))
    doc = merge_mod.build_document(ndl_pages, layout_pages, cfg, meta=meta)

    # ページ画像を IR に紐付け（あれば）。
    page_imgs = sorted(pages_out.glob("page_*.png"))
    for i, page in enumerate(doc.pages):
        if i < len(page_imgs):
            page.image_path = str(page_imgs[i])

    # 印刷された目次ページの範囲を推定（無料・規則ベース）。非表示化・突き合わせの起点。
    doc.meta.toc_pages = tocdetect_mod.detect_toc_pages(doc)

    # 書誌メタ取得（希望時のみ・ネット）。ファイル名から ISBN・出版社等を引く。
    if cfg.fetch_biblio:
        step(80, "書誌メタ（ISBN・出版社）を取得中…")
        from . import biblio as biblio_mod

        bm = biblio_mod.lookup(stem)
        if bm is not None:
            biblio_mod.apply_to_doc(doc, bm)

    # ルールベース本文整形（無料・既定ON。ページ境界の改行連結・ノンブル除去）。
    if cfg.text_cleanup:
        step(78, "本文を整形中（改行・ノンブル）…")
        textclean_mod.clean_document(doc)

    # LLM 構造化パス（任意）: 見出し階層＋異常検出＋本文リライト＋図alt。キー無しは no-op。
    if cfg.structure_llm.enabled:
        step(84, "AIで見出し構造を解析中…")
    review = structure_mod.run_structure_llm(doc, cfg.structure_llm)

    # 章番号の飛び・重複チェック（無料・規則ベース）。LLM の有無に関わらず付ける。
    seq = tocdetect_mod.heading_sequence_report(doc)
    doc.meta.structure_review = "\n\n".join(s for s in (review, seq) if s) or None

    return doc


def process_pdf(
    pdf_path: str | Path,
    cfg: PipelineConfig,
    *,
    run_engines: bool = True,
    on_step=None,
    on_output=None,
) -> Path:
    """1 PDF を Obsidian 用 Markdown まで変換。出力フォルダを返す。

    on_step(percent, label) は段階の進捗、on_output(line) はエンジン(NDL/Docling)の
    生の出力を1行ずつ受け取る（GUI のライブコンソール用）。
    """
    step = on_step or _noop_step
    pdf_path = Path(pdf_path)
    book = safe_book_name(pdf_path.stem)   # build_ir_for_pdf と同じ規則でフォルダ名を決める
    doc = build_ir_for_pdf(
        pdf_path, cfg, run_engines=run_engines, on_step=on_step, on_output=on_output
    )

    # 透明テキストPDFの名前を先に控える（見出し→PDFページリンクの張り先になる）。
    ndl_out = cfg.work_dir / book / "ndl"
    transparent_pdf = _first_glob(ndl_out, cfg.ndl.pdf_glob)
    if transparent_pdf is not None:
        doc.meta.pdf_name = transparent_pdf.name

    # IR を保存（GUI 補正の入力になる）。
    step(90, "中間データ(IR)を保存中…")
    ir_path = cfg.work_dir / book / "ir.json"
    ir_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(ir_path)

    step(93, "Markdownを書き出し中…")
    out_book = cfg.out_dir / book
    render_document(doc, out_book, cfg.render)
    _write_structure_review(doc, out_book)

    # NDL の透明テキストPDF（OCR結果の目視確認・別ツールでの再利用に使える）を
    # 作業ディレクトリから出力フォルダにもコピーしておく。
    step(98, "OCRテキスト付きPDFをコピー中…")
    if transparent_pdf is not None:
        dest_pdf = out_book / transparent_pdf.name
        shutil.copy2(transparent_pdf, dest_pdf)
        # 認識した見出しから PDF のしおり(アウトライン)を付ける。
        if cfg.pdf_bookmarks:
            add_pdf_bookmarks(dest_pdf, doc)

    step(100, "完了")
    return out_book


def _normalize_toc_levels(entries: list[list]) -> list[list]:
    """fitz.set_toc 用にレベルを正規化（先頭は1、増加は1段ずつ）。"""
    out: list[list] = []
    prev = 0
    for lvl, title, page in entries:
        lvl = max(1, int(lvl))
        if not out:
            lvl = 1
        elif lvl > prev + 1:
            lvl = prev + 1
        out.append([lvl, title, page])
        prev = lvl
    return out


def add_pdf_bookmarks(pdf_path: str | Path, doc: Document) -> bool:
    """透明テキストPDFに、IRの見出し→ページから PDF のしおり(アウトライン)を書き込む。

    PyMuPDF(fitz) が無ければ何もしない（False）。ページ番号は IR の 1 始まりを使い、
    PDF の実ページ数にクランプする。fitz はレベルの飛びに厳しいので正規化する。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return False

    headings = [
        (min(6, max(1, b.heading_level or 1)), b.text.strip(), page.number)
        for page in doc.pages
        for b in page.ordered_blocks()
        if b.type is BlockType.HEADING and b.text.strip()
    ]
    if not headings:
        return False
    try:
        with fitz.open(str(pdf_path)) as d:
            n = d.page_count
            toc = [[lvl, title[:200], min(max(1, pg), n)] for lvl, title, pg in headings]
            d.set_toc(_normalize_toc_levels(toc))
            d.saveIncr()
        return True
    except Exception:  # noqa: BLE001 - しおり付与の失敗で変換全体を止めない
        return False


def _write_structure_review(doc: Document, out_book: Path) -> None:
    """LLM 構成評価レポートがあれば out_book に書き出す（render 後に呼ぶこと）。

    render_document は out_dir の *.md を掃除するため、必ずレンダリング後に書く。
    """
    if doc.meta.structure_review:
        (out_book / "_structure_review.md").write_text(
            doc.meta.structure_review, encoding="utf-8"
        )


def postprocess_book(
    cfg: PipelineConfig,
    name: str,
    features: list[str],
    *,
    on_step=None,
    on_output=None,
) -> dict:
    """既存の変換結果（work/<name>/ir.json）に、選んだAI処理だけを後付けする。

    OCR・レイアウト解析はやり直さず、保存済み IR を読み込んで指定機能だけ実行し、
    再レンダリングする（速い・OCR課金ゼロ）。適用状況は status.json に記録する。
    features は estimate.FEATURES の部分集合（structure/rewrite/figure_alt/anomaly）。
    """
    from . import library
    from .estimate import estimate_feature
    from .llm import make_client
    from .pipeline import structure_llm as sl

    step = on_step or _noop_step
    ir_path = cfg.work_dir / name / "ir.json"
    if not ir_path.exists():
        raise FileNotFoundError(f"中間データが見つかりません: {ir_path}（先に通常変換が必要）")

    doc = Document.load(ir_path)
    client = make_client(cfg.structure_llm)
    if client is None:
        raise RuntimeError("Gemini APIキーが未設定です（設定でキーを入力してください）")

    # 実行前に概算を控えておき、記録に使う（実行後は figure_alt 等が0になるため）。
    pre_estimate = {f: estimate_feature(doc, f, cfg) for f in features}

    applied: list[str] = []
    sections: list[str] = []
    if "structure" in features:
        step(10, "AIで見出し構造を解析中…")
        sl.assign_hierarchy(doc, client, cfg.structure_llm)
        if cfg.structure_llm.review:
            r = sl.review_structure(doc, client, cfg.structure_llm)
            if r:
                sections.append("## 構成レビュー\n\n" + r)
        applied.append("structure")
    if "rewrite" in features:
        step(40, "本文をAIでリライト整形中…")
        sl.rewrite_body(doc, client, cfg.structure_llm)
        applied.append("rewrite")
    if "figure_alt" in features:
        step(65, "挿絵の代替テキストを生成中…")
        sl.describe_figures(doc, client, cfg.structure_llm)
        applied.append("figure_alt")
    if "anomaly" in features:
        step(82, "本文の異常をチェック中…")
        a = sl.check_anomalies(doc, client, cfg.structure_llm)
        if a:
            sections.append("## 本文の異常チェック\n\n" + a)
        applied.append("anomaly")

    if sections:
        doc.meta.structure_review = "\n\n".join(sections)

    step(90, "保存・再レンダリング中…")
    doc.save(ir_path)
    out_book = cfg.out_dir / name
    render_document(doc, out_book, cfg.render)
    _write_structure_review(doc, out_book)

    for f in applied:
        library.record_ai(cfg, name, f, pre_estimate.get(f, {}).get("jpy"))

    step(100, "完了")
    return {"applied": applied}


def apply_biblio(cfg: PipelineConfig, name: str, meta) -> dict:
    """選ばれた書誌メタ(BiblioMeta)を既存書籍に適用し、front-matterを更新して再出力する。

    OCR・レイアウトは触らない。ISBN があれば openBD で出版社・書影を補完してから書く。
    """
    from . import biblio as biblio_mod
    from . import library

    ir_path = cfg.work_dir / name / "ir.json"
    if not ir_path.exists():
        raise FileNotFoundError(f"中間データが見つかりません: {ir_path}（先に通常変換が必要）")
    doc = Document.load(ir_path)
    meta = biblio_mod.enrich_with_openbd(meta)
    biblio_mod.apply_to_doc(doc, meta)
    doc.save(ir_path)
    out_book = cfg.out_dir / name
    render_document(doc, out_book, cfg.render)
    _write_structure_review(doc, out_book)
    library.record_ai(cfg, name, "biblio", None)
    return {"ok": True, "isbn": meta.isbn13 or meta.isbn10, "publisher": meta.publisher}


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
    run_engines: bool = True,
) -> dict[str, str]:
    """フォルダ内の PDF を一括処理。結果マップ {pdf名: 状態} を返す。

    run_engines=False にすると、NDL/構造エンジンを起動せず既存の作業JSONから下流
    （merge・目次検出・LLM・レンダリング）だけ再実行する（PDCA の高速反復用）。
    その場合は skip_existing=False を併用しないと既存出力でスキップされる点に注意。
    """
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
        state = _process_with_retry(pdf, cfg, retries, progress, run_engines)
        results[pdf.name] = state
    return results


def _process_with_retry(pdf: Path, cfg: PipelineConfig, retries: int, progress,
                        run_engines: bool = True) -> str:
    last_err = ""
    for attempt in range(retries + 1):
        try:
            if progress:
                progress(pdf.name, f"processing (try {attempt + 1})")
            process_pdf(pdf, cfg, run_engines=run_engines)
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
