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

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from ..batch import process_pdf
from ..config import PipelineConfig, RenderConfig
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
    """1冊を処理。skip_existing で既存出力があればスキップ。

    失敗時はトレースバックをログファイルに残し、その場所を error メッセージへ
    添えたうえで再送出する（JobManager が status=error と全文を記録する）。
    """
    cfg: PipelineConfig = job.meta["cfg"]
    pdf_path = Path(job.path)
    out_book = cfg.out_dir / pdf_path.stem
    if job.meta.get("skip_existing", True) and (out_book / "00_index.md").exists():
        job.status = "skipped"
        return
    try:
        process_pdf(pdf_path, cfg)
    except Exception as e:
        log_path = _log_failure(pdf_path, e)
        if log_path is not None:
            job.meta["log_path"] = str(log_path)
        raise


manager = JobManager(_run_one)


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\n\t]', "_", name).strip() or "upload.pdf"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


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
    llm_structure: bool = Form(False),
    llm_anomaly: bool = Form(False),
    llm_rewrite: bool = Form(False),
    llm_figure_alt: bool = Form(False),
    llm_api_key: str = Form(""),
) -> dict:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    cfg = _base_config()
    if out_dir.strip():
        cfg.out_dir = Path(out_dir.strip())
    cfg.text_cleanup = text_cleanup
    cfg.render = RenderConfig(
        drop_headers_footers=drop_headers_footers,
        obsidian_wiklinks=obsidian_wiklinks,
        front_matter=front_matter,
        split_by_heading_level=1 if split_chapters else None,
    )
    if llm_structure:
        cfg.structure_llm.enabled = True
        cfg.structure_llm.anomaly_check = llm_anomaly
        cfg.structure_llm.rewrite_text = llm_rewrite
        cfg.structure_llm.figure_alt_text = llm_figure_alt
        if llm_api_key.strip():
            cfg.structure_llm.api_key = llm_api_key.strip()

    job_ids: list[str] = []
    for f in files:
        display = f.filename or f"upload_{uuid.uuid4().hex}.pdf"
        # 保存名は衝突しないよう一意化（表示名は元のまま）。
        saved = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{_safe_name(display)}"
        saved.write_bytes(await f.read())
        job_ids.append(
            manager.enqueue(
                filename=display,
                path=str(saved),
                output_dir=str(cfg.out_dir / Path(display).stem),
                meta={"cfg": cfg, "skip_existing": skip_existing},
            )
        )
    return {"job_ids": job_ids}


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
@app.get("/api/settings")
def get_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


@app.post("/api/settings")
async def save_settings(request: Request) -> dict:
    data = await request.json()
    if data.get("remember"):
        SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    elif SETTINGS_PATH.exists():
        # "記憶する" を外したら保存済みキーを消す。
        SETTINGS_PATH.unlink()
    return {"ok": True}
