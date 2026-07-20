"""GUI 用の FastAPI バックエンド。

処理は `fullocr.batch.process_folder` をそのまま呼ぶ。アップロードされた PDF は
作業ディレクトリに保存し、1つのバックグラウンドスレッドで順番に変換する
（複数ファイルを同時に処理すると、各変換自体がCPUを使い切ろうとして競合する
ため、あえて直列にしている）。フロントエンドは `/api/jobs` をポーリングして
進捗を表示する。
"""

from __future__ import annotations

import sys
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import HTMLResponse

from ..batch import process_folder
from ..config import PipelineConfig, RenderConfig

app = FastAPI(title="FullOCR")

STATIC_DIR = Path(__file__).parent / "static"


def _base_dir() -> Path:
    """出力・作業フォルダの基準ディレクトリ。

    凍結exe(ダブルクリック起動)では起動時の作業ディレクトリが不定なので、
    exe自身の場所を基準にして「必ず見つかる」場所に出力する。開発時は CWD。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()


UPLOAD_DIR = _base_dir() / "gui_uploads"

_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_batch_running = False


def _base_config() -> PipelineConfig:
    """ユーザーの fullocr.config.json（あれば）をベースに設定を組み立てる。

    PyInstaller で固めた exe の中では、NDLOCR-Lite は別licenseの同梱companion exe
    として `<このexeのフォルダ>/ndlocr-lite/ndlocr-lite.exe` に置かれる想定。
    起動時のカレントディレクトリに依存させたくないので、exe 自身の場所から
    絶対パスで解決する（`sys.executable` は凍結時、fullocr-gui.exe 自身を指す）。
    """
    config_path = Path("fullocr.config.json")
    cfg = PipelineConfig.load(config_path) if config_path.exists() else PipelineConfig()
    # 出力・作業フォルダを基準ディレクトリ配下の絶対パスにする（CWD 依存を排除し、
    # 「どこに保存されたか」を常に findable にする）。GUI で保存先が指定されたら
    # convert() 側で out_dir を上書きする。
    base = _base_dir()
    cfg.out_dir = base / "out"
    cfg.work_dir = base / "work"
    if getattr(sys, "frozen", False):
        ndl_exe = Path(sys.executable).parent / "ndlocr-lite" / "ndlocr-lite.exe"
        if ndl_exe.exists():
            cfg.ndl.command = f'"{ndl_exe}" --sourcepdf {{input}} --output {{outdir}}'
    return cfg


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
    out_dir: str = Form(""),
    llm_structure: bool = Form(False),
    llm_api_key: str = Form(""),
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

    cfg = _base_config()
    # GUI で保存先が指定されていればそれを使う（未指定なら基準ディレクトリの out/）。
    if out_dir.strip():
        cfg.out_dir = Path(out_dir.strip())
    # LLM 構造化（見出し階層＋評価）。キーは GUI 入力 or 環境変数 GEMINI_API_KEY。
    if llm_structure:
        cfg.structure_llm.enabled = True
        if llm_api_key.strip():
            cfg.structure_llm.api_key = llm_api_key.strip()
    cfg.render = RenderConfig(
        drop_headers_footers=drop_headers_footers,
        obsidian_wiklinks=obsidian_wiklinks,
        front_matter=front_matter,
        split_by_heading_level=1 if split_chapters else None,
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
