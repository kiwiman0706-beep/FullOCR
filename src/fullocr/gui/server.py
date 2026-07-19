"""GUI 用の FastAPI バックエンド。

処理は `fullocr.batch.process_folder` をそのまま呼ぶ。アップロードされた PDF は
作業ディレクトリに保存し、1つのバックグラウンドスレッドで順番に変換する
（複数ファイルを同時に処理すると、各変換自体がCPUを使い切ろうとして競合する
ため、あえて直列にしている）。フロントエンドは `/api/jobs` をポーリングして
進捗を表示する。
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import HTMLResponse

from ..batch import process_folder
from ..config import PipelineConfig, RenderConfig

app = FastAPI(title="FullOCR")

STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_DIR = Path("gui_uploads")

_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_batch_running = False


def _run_batch(upload_dir: Path, cfg: PipelineConfig, skip_existing: bool, job_id_by_name: dict[str, str]) -> None:
    global _batch_running

    def progress(name: str, state: str) -> None:
        job_id = job_id_by_name.get(name)
        if job_id is None:
            return
        with _lock:
            _jobs[job_id]["status"] = state

    try:
        process_folder(upload_dir, cfg, skip_existing=skip_existing, progress=progress)
    finally:
        with _lock:
            _batch_running = False


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
) -> dict:
    global _batch_running

    with _lock:
        if _batch_running:
            return {"error": "既に変換が実行中です。完了までお待ちください。"}
        _batch_running = True

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # 前回バッチの残骸を掃除してから今回のファイルだけを置く。
    for old in UPLOAD_DIR.glob("*.pdf"):
        old.unlink()

    cfg = PipelineConfig(
        render=RenderConfig(
            drop_headers_footers=drop_headers_footers,
            obsidian_wiklinks=obsidian_wiklinks,
            front_matter=front_matter,
            split_by_heading_level=1 if split_chapters else None,
        )
    )

    job_id_by_name: dict[str, str] = {}
    used_names: set[str] = set()
    for f in files:
        name = f.filename or f"upload_{uuid.uuid4().hex}.pdf"
        if name in used_names:
            # 同名ファイルの取り違え防止（ドラッグ&ドロップで同名PDFが混じった場合）。
            stem, dot, ext = name.rpartition(".")
            name = f"{stem or name}_{uuid.uuid4().hex[:6]}{dot}{ext}"
        used_names.add(name)

        content = await f.read()
        (UPLOAD_DIR / name).write_bytes(content)

        job_id = uuid.uuid4().hex
        job_id_by_name[name] = job_id
        with _lock:
            _jobs[job_id] = {
                "job_id": job_id,
                "filename": name,
                "status": "queued",
                "output_dir": str(cfg.out_dir / Path(name).stem),
            }

    thread = threading.Thread(
        target=_run_batch, args=(UPLOAD_DIR, cfg, skip_existing, job_id_by_name), daemon=True
    )
    thread.start()
    return {"job_ids": list(job_id_by_name.values())}


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    with _lock:
        return list(_jobs.values())


@app.get("/api/status")
def status() -> dict:
    with _lock:
        return {"running": _batch_running}
