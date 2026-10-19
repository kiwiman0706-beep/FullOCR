"""FullOCR コマンドラインインタフェース。

    fullocr init-config config.json      設定ファイルの雛形を出力
    fullocr run book.pdf                  1冊を変換
    fullocr run ./books --folder          フォルダ一括変換
    fullocr batch ./books                 「お任せ」一括変換（要約＋失敗一覧のみ表示）
    fullocr render work/book/ir.json      IR(補正済み含む)から Markdown 再生成
    fullocr eval work/                    目次(TOC)精度を評価（PDCAの物差し）
    fullocr digest work/book/             AI要約・マインドマップ・一問一答を生成（ソースリンク付き）
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .batch import extract_index_book, process_folder, process_pdf, render_from_ir, run_batch
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
            target, cfg, skip_existing=not no_skip, progress=_progress,
            run_engines=not no_engines,
        )
        for name, state in results.items():
            table.add_row(name, state)
        console.print(table)
        return

    with console.status(f"変換中: {target.name}"):
        out = process_pdf(target, cfg, run_engines=not no_engines)
    console.print(f"[green]完了:[/] {out}")


@app.command("batch")
def batch(
    folder: Path = typer.Argument(..., help="自炊PDFの入ったフォルダ"),
    config: Path | None = typer.Option(None, "--config", "-c", help="設定 JSON"),
    log: Path | None = typer.Option(None, "--log", help="詳細ログの出力先（既定 out/batch.log）"),
    no_engines: bool = typer.Option(
        False, "--no-engines", help="NDL/構造エンジンを起動せず既存の作業JSONから再構築"
    ),
    no_skip: bool = typer.Option(False, "--no-skip", help="既存出力をスキップしない"),
    retries: int = typer.Option(1, "--retries", help="1冊あたりの失敗リトライ回数"),
):
    """フォルダを丸ごと「お任せ」変換する（Claudeに投げる想定）。

    1冊ごとの進捗は**ログファイル**へ流し、画面には最後の要約と失敗一覧だけ出す。
    失敗した本は out/batch_report.json に原因付きで残るので、そこだけ後追いすればよい。
    """
    cfg = _load_cfg(config)
    with console.status(f"一括変換中: {folder}（詳細はログファイルへ）"):
        rep = run_batch(
            folder, cfg, log_path=log, skip_existing=not no_skip,
            run_engines=not no_engines, retries=retries,
        )
    console.print(
        f"[bold]変換バッチ完了[/]  合計 {rep['total']} 冊 / {rep['elapsed_sec']:.0f}秒"
    )
    console.print(
        f"  [green]成功 {len(rep['done'])}[/]  スキップ {len(rep['skipped'])}  "
        f"[red]失敗 {len(rep['failed'])}[/]"
    )
    if rep["failed"]:
        table = Table("失敗した本", "原因")
        for f in rep["failed"]:
            table.add_row(f["name"], (f["error"] or "")[:120])
        console.print(table)
    console.print(f"詳細ログ: {rep['log']}")
    console.print(f"失敗一覧(JSON): {rep['report']}")


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


@app.command("index-terms")
def index_terms(
    target: Path = typer.Argument(..., help="ir.json、work/<book>/、または work/（全書籍）"),
    config: Path | None = typer.Option(None, "--config", "-c", help="設定 JSON（APIキー）"),
):
    """巻末索引→見出し語リスト(表記のみ)を抽出（Stage 1）。out/<book>/_index_terms.txt。

    読みの付与(Stage 2)は別スレッド(Claude)で。Gemini APIキーが必要
    （config の structure_llm.api_key か 環境変数 GEMINI_API_KEY）。
    """
    cfg = _load_cfg(config)

    def _one(work_dir: Path, name: str) -> None:
        cfg.work_dir = work_dir
        try:
            r = extract_index_book(cfg, name)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]{name}: {e}[/]")
            return
        if not r["found"]:
            console.print(f"[yellow]{name}: 索引ページなし[/]")
        else:
            console.print(f"[green]{name}: {r['count']}語[/] → {r['file']}")

    ir = target if target.suffix == ".json" else target / "ir.json"
    if ir.exists():
        _one(ir.parent.parent, ir.parent.name)
        return
    books = sorted(p for p in target.glob("*/ir.json"))
    if not books:
        console.print(f"[red]ir.json が見つかりません:[/] {target}")
        raise typer.Exit(1)
    for p in books:
        _one(p.parent.parent, p.parent.name)


@app.command("digest")
def digest_cmd(
    target: Path = typer.Argument(..., help="ir.json、work/<book>/、または work/（全書籍）"),
    config: Path | None = typer.Option(None, "--config", "-c", help="設定 JSON（APIキー）"),
    kinds: str = typer.Option("summary,mindmap,qa", "--kinds",
                              help="生成種別をカンマ区切りで（summary/mindmap/qa）"),
    epub: bool = typer.Option(False, "--epub", help="EPUB も出力（本文同梱・内部リンク）"),
):
    """AI要約・マインドマップ・一問一答を、各項目にソースリンク付きで生成（Stage後）。

    out/<book>/_digest.md（と --epub 指定時は _digest.epub）。再OCRは不要。
    Gemini APIキーが必要（config の structure_llm.api_key か 環境変数 GEMINI_API_KEY）。
    """
    from .digest import digest_book

    cfg = _load_cfg(config)
    cfg.digest.kinds = [k.strip() for k in kinds.split(",") if k.strip()]

    def _one(work_dir: Path, name: str) -> None:
        cfg.work_dir = work_dir
        try:
            r = digest_book(cfg, name, make_epub=epub)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]{name}: {e}[/]")
            return
        extra = f" + {r['epub']}" if "epub" in r else ""
        console.print(f"[green]{name}: {r['sections']}節[/] → {r['md']}{extra}")

    ir = target if target.suffix == ".json" else target / "ir.json"
    if ir.exists():
        _one(ir.parent.parent, ir.parent.name)
        return
    books = sorted(p for p in target.glob("*/ir.json"))
    if not books:
        console.print(f"[red]ir.json が見つかりません:[/] {target}")
        raise typer.Exit(1)
    for p in books:
        _one(p.parent.parent, p.parent.name)


@app.command("gui")
def gui():
    """ローカル GUI（ドラッグ&ドロップでバッチ変換）を起動する。"""
    from .gui.app import main as gui_main

    gui_main()


if __name__ == "__main__":
    app()
