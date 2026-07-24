"""FullOCR コマンドラインインタフェース。

    fullocr init-config config.json      設定ファイルの雛形を出力
    fullocr run book.pdf                  1冊を変換
    fullocr run ./books --folder          フォルダ一括変換
    fullocr render work/book/ir.json      IR(補正済み含む)から Markdown 再生成
    fullocr eval work/                    目次(TOC)精度を評価（PDCAの物差し）
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .batch import process_folder, process_pdf, render_from_ir
from .config import PipelineConfig
from .ir import Document

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


@app.command("eval")
def eval_toc(
    target: Path = typer.Argument(..., help="ir.json、work/<book>/、または work/（全書籍を集計）"),
    as_json: bool = typer.Option(False, "--json", help="JSON で出力（スクリプト集計用）"),
):
    """目次(TOC)精度を評価する（OCRは再実行しない）。PDCAの物差し。

    - ir.json か work/<book>/ を渡すと1冊の詳細レポート。
    - work/（配下に <book>/ir.json が複数）を渡すと全書籍の要約テーブル＋平均。
    """
    import json as _json

    from .eval import evaluate_document

    def _report(ir_path: Path, name: str):
        return evaluate_document(Document.load(ir_path), book=name)

    # 1冊: ir.json か <book>/ir.json
    ir = target if target.suffix == ".json" else target / "ir.json"
    if ir.exists():
        # ファイル名が ir.json なら親フォルダ名を書名ラベルにする。
        label = ir.parent.name if ir.stem == "ir" else ir.stem
        rep = _report(ir, label)
        if as_json:
            console.print_json(rep.model_dump_json())
            return
        console.print(f"[bold]{rep.book}[/]  pages={rep.pages}  toc_pages={rep.toc_pages}")
        console.print(f"見出し数={rep.num_headings}  レベル分布={rep.level_hist}")
        console.print(
            f"目次項目={rep.toc_entries}  一致={rep.matched}  "
            f"[green]toc_recall={rep.toc_recall}[/]  heading_precision={rep.heading_precision}  "
            f"連番問題={rep.sequence_issues}"
        )
        if rep.missing:
            console.print(f"[yellow]目次にあるが見出しに無い:[/] {rep.missing[:15]}")
        if rep.extra:
            console.print(f"[cyan]見出しだが目次に無い:[/] {rep.extra[:15]}")
        return

    # 複数冊: work/ 配下
    books = sorted(p for p in target.glob("*/ir.json"))
    if not books:
        console.print(f"[red]ir.json が見つかりません:[/] {target}")
        raise typer.Exit(1)
    reports = [_report(p, p.parent.name) for p in books]
    if as_json:
        console.print_json(_json.dumps([r.model_dump() for r in reports], ensure_ascii=False))
        return
    table = Table("書籍", "見出し", "目次項目", "recall", "precision", "連番問題")
    for r in reports:
        table.add_row(r.book, str(r.num_headings), str(r.toc_entries),
                      str(r.toc_recall), str(r.heading_precision), str(r.sequence_issues))
    console.print(table)
    recalls = [r.toc_recall for r in reports if r.toc_recall is not None]
    if recalls:
        console.print(f"[bold green]平均 toc_recall = {sum(recalls) / len(recalls):.3f}[/] "
                      f"（目次を検出できた {len(recalls)}/{len(reports)} 冊）")


@app.command("gui")
def gui():
    """ローカル GUI（ドラッグ&ドロップでバッチ変換）を起動する。"""
    from .gui.app import main as gui_main

    gui_main()


if __name__ == "__main__":
    app()
