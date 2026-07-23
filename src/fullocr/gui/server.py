"""GUI 用の FastAPI バックエンド。

1冊 = 1ジョブとして JobManager（gui/jobs.py）が直列処理する（各変換が CPU を使い
切るため並列にしない）。フロントは /api/jobs をポーリングして進捗を表示し、
一時停止・中止・スキップ・並べ替えを各エンドポイントで制御する。

API キー等の設定は base_dir の fullocr_gui_settings.json にローカル保存できる
（"記憶する" 時のみ）。このファイルは配布物・リポジトリには含めない。
"""

from __future__ import annotations

import json
import re
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from .. import __version__
from .. import estimate as estimate_mod
from .. import library as library_mod
from .. import biblio as biblio_mod
from ..batch import apply_biblio, postprocess_book, process_pdf
from ..config import PipelineConfig, RenderConfig
from ..ir import Document
from .jobs import Job, JobManager

app = FastAPI(title="FullOCR")

STATIC_DIR = Path(__file__).parent / "static"


def _frozen_dir() -> Path:
    """凍結時の実行ファイルの所在（同梱 companion の解決に使う）。

    macOS .app では `FullOCR.app/Contents/MacOS/<exe>`、Windows/Linux の onedir では
    exe と同じフォルダ。いずれも `sys.executable` の親でよい。
    """
    return Path(sys.executable).parent


def _base_dir() -> Path:
    """出力・作業フォルダの基準ディレクトリ（ユーザーに見える書込可能な場所）。

    インストーラで Program Files や /Applications に入れると、そこは書込不可
    （または .app 内に隠れる）ので、凍結時はユーザーのホーム配下 `~/FullOCR` を
    基準にする（out/ work/ gui_uploads/ 設定 をここに置く）。開発時は CWD。
    保存先は GUI で個別に指定可能（Obsidian 保管庫など）。
    """
    if getattr(sys, "frozen", False):
        base = Path.home() / "FullOCR"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path.cwd()


UPLOAD_DIR = _base_dir() / "gui_uploads"
SETTINGS_PATH = _base_dir() / "fullocr_gui_settings.json"


def _base_config() -> PipelineConfig:
    """ユーザーの fullocr.config.json（あれば）をベースに設定を組み立てる。

    PyInstaller で固めたアプリの中では、NDLOCR-Lite は別licenseの同梱 companion
    実行ファイルとして `<実行ファイルのフォルダ>/ndlocr-lite/` に置かれる想定
    （Windows は ndlocr-lite.exe、macOS/Linux は ndlocr-lite）。
    """
    config_path = Path("fullocr.config.json")
    cfg = PipelineConfig.load(config_path) if config_path.exists() else PipelineConfig()
    base = _base_dir()
    cfg.out_dir = base / "out"
    cfg.work_dir = base / "work"
    if getattr(sys, "frozen", False):
        exe_name = "ndlocr-lite.exe" if sys.platform.startswith("win") else "ndlocr-lite"
        ndl_exe = _frozen_dir() / "ndlocr-lite" / exe_name
        if ndl_exe.exists():
            cfg.ndl.command = f'"{ndl_exe}" --sourcepdf {{input}} --output {{outdir}}'
    return cfg


def _log_failure(pdf_path: Path, err: BaseException) -> Path | None:
    """失敗した1冊のトレースバックを base_dir/logs/ にファイル保存し、その場所を返す。

    GUI を閉じていても後から原因を追えるよう、ディスクにも残す。書き込み自体に
    失敗しても本処理のエラーを覆い隠さないよう、握りつぶして None を返す。
    """
    try:
        log_dir = _base_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = log_dir / f"{pdf_path.stem}_{stamp}.log"
        log_path.write_text(
            f"file: {pdf_path}\ntime: {stamp}\n\n"
            + "".join(traceback.format_exception(type(err), err, err.__traceback__)),
            encoding="utf-8",
        )
        return log_path
    except OSError:
        return None


def _run_one(job: Job) -> None:
    """1ジョブを処理。kind に応じて通常変換 or 後付けAI処理を実行する。

    失敗時はトレースバックをログファイルに残し、その場所を error メッセージへ
    添えたうえで再送出する（JobManager が status=error と全文を記録する）。
    """
    cfg: PipelineConfig = job.meta["cfg"]
    kind = job.meta.get("kind", "convert")

    def _on_step(percent: float, label: str) -> None:
        manager.progress(job.id, percent, label)

    def _on_output(line: str) -> None:
        manager.log_line(job.id, line)

    if kind == "postprocess":
        name = job.meta["name"]
        try:
            postprocess_book(cfg, name, job.meta["features"],
                             on_step=_on_step, on_output=_on_output)
        except Exception as e:
            log_path = _log_failure(Path(name), e)
            if log_path is not None:
                job.meta["log_path"] = str(log_path)
            raise
        return

    # kind == "convert"
    pdf_path = Path(job.path)
    out_book = cfg.out_dir / pdf_path.stem
    if job.meta.get("skip_existing", True) and (out_book / "00_index.md").exists():
        job.status = "skipped"
        return
    try:
        process_pdf(pdf_path, cfg, on_step=_on_step, on_output=_on_output)
    except Exception as e:
        log_path = _log_failure(pdf_path, e)
        if log_path is not None:
            job.meta["log_path"] = str(log_path)
        raise


manager = JobManager(_run_one)


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\n\t]', "_", name).strip() or "upload.pdf"


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # アップデート後に古いページが埋め込みブラウザにキャッシュされて「変わっていない」
    # ように見えるのを防ぐ（毎回サーバから読み直させる）。
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/version")
def version() -> dict:
    """画面に出すアプリのバージョン（ビルド時に __init__.py へ焼き込まれる）。"""
    return {"version": __version__}


@app.post("/api/convert")
async def convert(
    files: list[UploadFile],
    drop_headers_footers: bool = Form(True),
    obsidian_wiklinks: bool = Form(True),
    front_matter: bool = Form(True),
    split_chapters: bool = Form(False),
    skip_existing: bool = Form(True),
    out_dir: str = Form(""),
    text_cleanup: bool = Form(True),
    hide_toc_pages: bool = Form(False),
    pdf_bookmarks: bool = Form(True),
    fetch_biblio: bool = Form(False),
    llm_structure: bool = Form(False),
    llm_anomaly: bool = Form(False),
    llm_rewrite: bool = Form(False),
    llm_figure_alt: bool = Form(False),
    llm_toc_image: bool = Form(False),
    llm_api_key: str = Form(""),
) -> dict:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    cfg = _base_config()
    if out_dir.strip():
        cfg.out_dir = Path(out_dir.strip())
    cfg.text_cleanup = text_cleanup
    cfg.pdf_bookmarks = pdf_bookmarks
    cfg.fetch_biblio = fetch_biblio
    cfg.render = RenderConfig(
        drop_headers_footers=drop_headers_footers,
        obsidian_wiklinks=obsidian_wiklinks,
        front_matter=front_matter,
        split_by_heading_level=1 if split_chapters else None,
        hide_toc_pages=hide_toc_pages,
    )
    if llm_structure:
        cfg.structure_llm.enabled = True
        cfg.structure_llm.anomaly_check = llm_anomaly
        cfg.structure_llm.rewrite_text = llm_rewrite
        cfg.structure_llm.figure_alt_text = llm_figure_alt
        cfg.structure_llm.toc_image_reference = llm_toc_image
        if llm_api_key.strip():
            cfg.structure_llm.api_key = llm_api_key.strip()

    job_ids: list[str] = []
    for f in files:
        display = f.filename or f"upload_{uuid.uuid4().hex}.pdf"
        # 保存名は衝突しないよう一意化（表示名は元のまま）。
        saved = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{_safe_name(display)}"
        saved.write_bytes(await f.read())
        # リトライ用に元PDFの場所を status.json に控える。
        stem = Path(display).stem
        data = library_mod.load_status(cfg, stem)
        data["source_pdf"] = str(saved)
        library_mod.save_status(cfg, stem, data)
        job_ids.append(
            manager.enqueue(
                filename=display,
                path=str(saved),
                output_dir=str(cfg.out_dir / stem),
                meta={"cfg": cfg, "skip_existing": skip_existing, "kind": "convert"},
            )
        )
    return {"job_ids": job_ids}


def _library_cfg(out_dir: str = "") -> PipelineConfig:
    """ライブラリ/概算/後付け用の設定。out_dir 指定があればそれを見る。

    work_dir（ir.json・status.json の置き場）は常に既定（base/work）。out_dir は
    ユーザーが選んだ保存先（Obsidian保管庫など）で、生成物の有無判定と再出力先に使う。
    """
    cfg = _base_config()
    if out_dir.strip():
        cfg.out_dir = Path(out_dir.strip())
    return cfg


def _merged_library(cfg: PipelineConfig) -> list[dict]:
    """ディスク上の書籍一覧に、実行中/待機/失敗ジョブのライブ状態を重ねる。"""
    books = library_mod.scan_library(cfg)
    by_name: dict[str, dict] = {b["name"]: b for b in books}
    for j in manager.list_jobs():
        name = Path(j["filename"]).stem
        book = by_name.get(name)
        if book is None:
            book = {
                "name": name, "out_dir": str(cfg.out_dir / name), "has_ir": False,
                "stages": {"ocr": "none", "layout": "none", "render": "none"},
                "ai": {f: {"done": False, "jpy": None, "at": None} for f in estimate_mod.FEATURES},
                "source": "job",
            }
            by_name[name] = book
        book["job"] = j  # percent/step/status/error/traceback/log_path/job_id/pending
    # 実行中・待機を上に、その後は名前順。
    order = {"processing": 0, "queued": 1, "error": 2}
    return sorted(
        by_name.values(),
        key=lambda b: (order.get((b.get("job") or {}).get("status"), 3), b["name"]),
    )


@app.get("/api/library")
def library(out_dir: str = "") -> list[dict]:
    return _merged_library(_library_cfg(out_dir))


@app.post("/api/estimate")
async def estimate(request: Request) -> dict:
    """1冊のAI各処理の概算（¥）を返す。ir.json が無ければ applicable=false。"""
    data = await request.json()
    name = (data.get("book") or "").strip()
    cfg = _library_cfg(data.get("out_dir", ""))
    ir_path = cfg.work_dir / name / "ir.json"
    if not name or not ir_path.exists():
        return {"ok": False, "error": "中間データ(ir.json)がありません", "estimates": {}}
    doc = Document.load(ir_path)
    return {"ok": True, "estimates": estimate_mod.estimate_all(doc, cfg)}


@app.post("/api/postprocess")
async def postprocess(request: Request) -> dict:
    """既存書籍に選んだAI処理を後付けする（ジョブとして実行）。"""
    data = await request.json()
    name = (data.get("book") or "").strip()
    features = [f for f in (data.get("features") or []) if f in estimate_mod.FEATURES]
    if not name or not features:
        return {"ok": False, "error": "書籍または処理が指定されていません"}
    cfg = _library_cfg(data.get("out_dir", ""))
    # APIキー: リクエスト → 保存設定 の順。
    key = (data.get("llm_api_key") or "").strip() or _saved_api_key()
    if key:
        cfg.structure_llm.api_key = key
    cfg.structure_llm.enabled = True
    # 目次画像参照は保存設定のトグルに従う（structure 実行時に効く）。
    cfg.structure_llm.toc_image_reference = bool(_saved_settings().get("opt", {}).get("opt-llm-tocimg"))
    job_id = manager.enqueue(
        filename=name, path=name, output_dir=str(cfg.out_dir / name),
        meta={"cfg": cfg, "kind": "postprocess", "name": name, "features": features},
    )
    return {"ok": True, "job_id": job_id}


@app.post("/api/biblio_search")
def biblio_search(payload: dict) -> dict:
    """書名（ファイル名）から書誌候補を Google Books / NDL で検索して返す。

    ネットを叩くが sync 定義なので FastAPI がスレッドプールで実行（サーバは固まらない）。
    """
    name = (payload.get("book") or "").strip()
    if not name:
        return {"ok": False, "error": "書籍が指定されていません", "candidates": []}
    try:
        cands = biblio_mod.search_candidates(name)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"検索に失敗しました: {e}", "candidates": []}
    return {"ok": True, "candidates": [c.model_dump() for c in cands]}


@app.post("/api/biblio_apply")
def biblio_apply(payload: dict) -> dict:
    """選ばれた候補を書籍に適用して front-matter を更新・再出力する。"""
    name = (payload.get("book") or "").strip()
    cand = payload.get("candidate") or {}
    cfg = _library_cfg(payload.get("out_dir", ""))
    if not name or not cand:
        return {"ok": False, "error": "書籍または候補が指定されていません"}
    if not (cfg.work_dir / name / "ir.json").exists():
        return {"ok": False, "error": "中間データ(ir.json)がありません"}
    try:
        meta = biblio_mod.BiblioMeta(**cand)
        result = apply_biblio(cfg, name, meta)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"適用に失敗しました: {e}"}
    return result


def _safe_child(base: Path, rel: str) -> Path | None:
    """base 配下に収まるパスだけ返す（パストラバーサル防止）。"""
    try:
        p = (base / rel).resolve()
        base_r = base.resolve()
    except OSError:
        return None
    if p == base_r or base_r in p.parents:
        return p
    return None


@app.post("/api/preview")
async def preview(request: Request) -> dict:
    """生成済み Markdown をアプリ内プレビュー用に返す（ファイル一覧＋本文）。"""
    data = await request.json()
    name = (data.get("book") or "").strip()
    cfg = _library_cfg(data.get("out_dir", ""))
    out_book = cfg.out_dir / name
    if not name or not out_book.is_dir():
        return {"ok": False, "error": "出力が見つかりません（先に変換が必要）"}
    files = sorted(p.name for p in out_book.glob("*.md"))
    if not files:
        return {"ok": False, "error": "Markdown がまだありません"}
    fname = (data.get("file") or "").strip()
    if fname not in files:
        fname = "00_index.md" if "00_index.md" in files else files[0]
    target = _safe_child(out_book, fname)
    if target is None or not target.is_file():
        return {"ok": False, "error": "ファイルが見つかりません"}
    return {"ok": True, "files": files, "file": fname,
            "content": target.read_text(encoding="utf-8")}


@app.get("/api/asset", response_model=None)
def asset(book: str, path: str, out_dir: str = "") -> Response:
    """プレビュー内の画像等を配信（out_dir/book 配下限定）。"""
    cfg = _library_cfg(out_dir)
    out_book = cfg.out_dir / book
    target = _safe_child(out_book, path)
    if target is None or not target.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(str(target))


@app.post("/api/retry")
async def retry(request: Request) -> dict:
    """失敗した通常変換を、控えておいた元PDFから再実行する。"""
    data = await request.json()
    name = (data.get("book") or "").strip()
    cfg = _library_cfg(data.get("out_dir", ""))
    src = library_mod.load_status(cfg, name).get("source_pdf", "")
    if not src or not Path(src).exists():
        return {"ok": False, "error": "元PDFが見つかりません。もう一度ドロップしてください。"}
    job_id = manager.enqueue(
        filename=f"{name}.pdf", path=src, output_dir=str(cfg.out_dir / name),
        meta={"cfg": cfg, "skip_existing": False, "kind": "convert"},
    )
    return {"ok": True, "job_id": job_id}


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return manager.list_jobs()


@app.get("/api/status")
def status() -> dict:
    return manager.status()


@app.post("/api/pause")
def pause() -> dict:
    manager.pause()
    return manager.status()


@app.post("/api/resume")
def resume() -> dict:
    manager.resume()
    return manager.status()


@app.post("/api/stop")
def stop() -> dict:
    manager.stop()
    return manager.status()


@app.post("/api/jobs/{job_id}/skip")
def skip_job(job_id: str) -> dict:
    return {"ok": manager.skip(job_id)}


@app.post("/api/jobs/{job_id}/move")
def move_job(job_id: str, direction: str = "up") -> dict:
    return {"ok": manager.move(job_id, "up" if direction != "down" else "down")}


# ---- 設定の保存/読み込み（API キー等のローカル保持。任意） -----------------
def _saved_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _saved_api_key() -> str:
    return (_saved_settings().get("llm_api_key") or "").strip()


@app.get("/api/settings")
def get_settings() -> dict:
    return _saved_settings()


@app.post("/api/settings")
async def save_settings(request: Request) -> dict:
    data = await request.json()
    if data.get("remember"):
        SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    elif SETTINGS_PATH.exists():
        # "記憶する" を外したら保存済みキーを消す。
        SETTINGS_PATH.unlink()
    return {"ok": True}
