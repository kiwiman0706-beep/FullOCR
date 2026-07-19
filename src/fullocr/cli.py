"""FullOCR コマンドラインインタフェース。

    fullocr init-config config.json      設定ファイルの雛形を出力
    fullocr run book.pdf                  1冊を変換
    fullocr run ./books --folder          フォルダ一括変換
    fullocr render work/book/ir.json      IR(補正済み含む)から Markdown 再生成
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .batch import process_folder, process_pdf, render_from_ir
from .config import PipelineConfig

app = typer.Typer(add_completion=False, help="自炊PDF → 高精度Markdown (NDLOCR-Lite + Marker)")
console = Console()


def _load_cfg(config: Path | None) -> PipelineConfig:
    if config and config.exists():
        return PipelineConfig.load(config)
    return PipelineConfig()


@app.command("init-config")
def init_config(path: Path = typer.Argument(Path("fullocr.config.json"))):
    """設定ファイルの雛形を書き出す。"""
    cfg = PipelineConfig()
    cfg.save(path)
    console.print(f"[green]設定雛形を書き出しました:[/] {path}")
    console.print("NDL/Marker の command 行を、実際のインストール状況に合わせて調整してください。")


@app.command("run")
def run(
    target: Path = typer.Argument(..., help="PDF ファイル、または --folder 指定時はフォルダ"),
    folder: bool = typer.Option(False, "--folder", help="フォルダ内の PDF を一括処理"),
    config: Path | None = typer.Option(None, "--config", "-c", help="設定 JSON"),
    no_engines: bool = typer.Option(
        False, "--no-engines", help="NDL/Marker を起動せず既存の作業JSONから再構築"
    ),
    no_skip: bool = typer.Option(False, "--no-skip", help="既存出力をスキップしない"),
):
    """PDF を Obsidian 用 Markdown に変換する。"""
    cfg = _load_cfg(config)

    if folder:
        table = Table("PDF", "状態")

        def _progress(name: str, state: str) -> None:
            console.print(f"  {name}: {state}")

        results = process_folder(
            target, cfg, skip_existing=not no_skip, progress=_progress
        )
        for name, state in results.items():
            table.add_row(name, state)
        console.print(table)
        return

    with console.status(f"変換中: {target.name}"):
        out = process_pdf(target, cfg, run_engines=not no_engines)
    console.print(f"[green]完了:[/] {out}")


@app.command("render")
def render(
    ir: Path = typer.Argument(..., help="IR JSON（GUI補正後など）"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    name: str | None = typer.Option(None, "--name", help="出力フォルダ名"),
):
    """IR JSON から Markdown を再生成する（OCRは再実行しない）。"""
    cfg = _load_cfg(config)
    out = render_from_ir(ir, cfg, out_name=name)
    console.print(f"[green]再生成完了:[/] {out}")


@app.command("gui")
def gui():
    """ローカル GUI（ドラッグ&ドロップでバッチ変換）を起動する。"""
    from .gui.app import main as gui_main

    gui_main()


if __name__ == "__main__":
    app()
