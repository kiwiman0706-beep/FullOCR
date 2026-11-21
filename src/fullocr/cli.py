"""FullOCR コマンドラインインタフェース。

    fullocr init-config config.json      設定ファイルの雛形を出力
    fullocr run book.pdf                  1冊を変換
    fullocr run ./books --folder          フォルダ一括変換
    fullocr batch ./books                 「お任せ」一括変換（要約＋失敗一覧のみ表示）
    fullocr render work/book/ir.json      IR(補正済み含む)から Markdown 再生成
    fullocr eval work/                    目次(TOC)精度を評価（PDCAの物差し）
    fullocr estimate work/                AI処理の費用概算を全書籍で集計（課金なし）
    fullocr digest work/book/             AI要約・マインドマップ・一問一答を生成（ソースリンク付き）
    fullocr pack work/book/               1ファイルの .jisui（ZIP）にまとめる
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


def _load_cfg(config: Path | None, preset: str | None = None) -> PipelineConfig:
    cfg = PipelineConfig.load(config) if config and config.exists() else PipelineConfig()
    if preset:
        from .presets import apply_preset

        try:
            cfg = apply_preset(cfg, preset)
        except KeyError as e:
            raise typer.BadParameter(str(e.args[0])) from e
    return cfg


_PRESET_HELP = "用途別プリセットを設定の上から重ねる（bookhatch / obsidian。`fullocr presets` で一覧）"


@app.command("init-config")
def init_config(
    path: Path = typer.Argument(Path("fullocr.config.json")),
    preset: str | None = typer.Option(None, "--preset", "-p", help=_PRESET_HELP),
):
    """設定ファイルの雛形を書き出す（--preset で用途別の推奨値入り）。"""
    cfg = _load_cfg(None, preset)
    cfg.save(path)
    console.print(f"[green]設定雛形を書き出しました:[/] {path}")
    console.print("NDL/Marker の command 行を、実際のインストール状況に合わせて調整してください。")


@app.command("presets")
def presets_cmd():
    """用途別プリセットの一覧。"""
    from .presets import PRESETS

    for name, (desc, _) in PRESETS.items():
        console.print(f"[bold]{name}[/]  {desc}")


@app.command("run")
def run(
    target: Path = typer.Argument(..., help="PDF ファイル、または --folder 指定時はフォルダ"),
    folder: bool = typer.Option(False, "--folder", help="フォルダ内の PDF を一括処理"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="サブフォルダも辿る（--folder時）"),
    config: Path | None = typer.Option(None, "--config", "-c", help="設定 JSON"),
    no_engines: bool = typer.Option(
        False, "--no-engines", help="NDL/Marker を起動せず既存の作業JSONから再構築"
    ),
    no_skip: bool = typer.Option(False, "--no-skip", help="既存出力をスキップしない"),
    preset: str | None = typer.Option(None, "--preset", "-p", help=_PRESET_HELP),
):
    """PDF を Obsidian 用 Markdown に変換する。"""
    cfg = _load_cfg(config, preset)

    if folder:
        table = Table("PDF", "状態")

        def _progress(name: str, state: str) -> None:
            console.print(f"  {name}: {state}")

        results = process_folder(
            target, cfg, recursive=recursive, skip_existing=not no_skip,
            progress=_progress, run_engines=not no_engines,
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
    recursive: bool = typer.Option(False, "--recursive", "-r", help="サブフォルダも辿る"),
    no_engines: bool = typer.Option(
        False, "--no-engines", help="NDL/構造エンジンを起動せず既存の作業JSONから再構築"
    ),
    no_skip: bool = typer.Option(False, "--no-skip", help="既存出力をスキップしない"),
    retries: int = typer.Option(1, "--retries", help="1冊あたりの失敗リトライ回数"),
    preset: str | None = typer.Option(None, "--preset", "-p", help=_PRESET_HELP),
):
    """フォルダを丸ごと「お任せ」変換する（Claudeに投げる想定）。

    1冊ごとの進捗は**ログファイル**へ流し、画面には最後の要約と失敗一覧だけ出す。
    失敗した本は out/batch_report.json に原因付きで残るので、そこだけ後追いすればよい。
    """
    cfg = _load_cfg(config, preset)
    with console.status(f"一括変換中: {folder}（詳細はログファイルへ）"):
        rep = run_batch(
            folder, cfg, log_path=log, recursive=recursive, skip_existing=not no_skip,
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


@app.command("estimate")
def estimate_cmd(
    target: Path = typer.Argument(..., help="ir.json、work/<book>/、または work/（全書籍を集計）"),
    config: Path | None = typer.Option(None, "--config", "-c", help="設定 JSON（単価）"),
    feature: str = typer.Option("structure,digest", "--feature",
                                help="集計する機能をカンマ区切り（structure/digest/rewrite/figure_alt/anomaly）"),
    as_json: bool = typer.Option(False, "--json", help="JSON で出力"),
):
    """AI処理の費用概算を全書籍で集計する（OCR/LLMは呼ばない・課金なし）。

    後で一括でAI構造化(structure)や要約(digest)を回す前に、「全部やると概算いくらか」を
    把握するための物差し。out/estimate_report.json にも保存する。
    """
    import json as _json

    from .estimate import estimate_feature

    feats = [f.strip() for f in feature.split(",") if f.strip()]
    cfg = _load_cfg(config)

    ir = target if target.suffix == ".json" else target / "ir.json"
    if ir.exists():
        books = [ir]
    else:
        books = sorted(target.glob("*/ir.json"))
    if not books:
        console.print(f"[red]ir.json が見つかりません:[/] {target}")
        raise typer.Exit(1)

    per_book: list[dict] = []
    totals: dict[str, float] = {f: 0.0 for f in feats}
    counts: dict[str, int] = {f: 0 for f in feats}
    for p in books:
        doc = Document.load(p)
        name = p.parent.name
        row: dict = {"book": name, "pages": len(doc.pages)}
        for f in feats:
            est = estimate_feature(doc, f, cfg)
            row[f] = est["jpy"]
            if est["applicable"]:
                totals[f] += est["jpy"]
                counts[f] += 1
        per_book.append(row)

    report = {
        "books": len(books),
        "feature_totals_jpy": {f: round(totals[f], 1) for f in feats},
        "feature_applicable_books": counts,
        "grand_total_jpy": round(sum(totals.values()), 1),
        "per_book": per_book,
        "price": {"model": cfg.structure_llm.model, "usd_jpy": cfg.cost.usd_jpy,
                  "in_per_mtok": cfg.cost.price_in_per_mtok, "out_per_mtok": cfg.cost.price_out_per_mtok},
    }
    report_path = cfg.out_dir / "estimate_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if as_json:
        console.print_json(_json.dumps(report, ensure_ascii=False))
        return
    table = Table("書籍", "ページ", *feats)
    for row in per_book:
        table.add_row(row["book"], str(row["pages"]), *[f"¥{row[f]:g}" for f in feats])
    console.print(table)
    console.print(f"[bold]合計 {len(books)}冊[/]  " +
                  "  ".join(f"{f}: [green]¥{totals[f]:,.0f}[/]（{counts[f]}冊）" for f in feats))
    console.print(f"[bold]総計 ¥{report['grand_total_jpy']:,.0f}[/]  "
                  f"（{cfg.structure_llm.model} / ${cfg.cost.price_in_per_mtok}・${cfg.cost.price_out_per_mtok} per Mtok, "
                  f"¥{cfg.cost.usd_jpy}/$）")
    console.print(f"保存: {report_path}")


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


@app.command("pack")
def pack_cmd(
    target: Path = typer.Argument(..., help="ir.json、work/<book>/、または work/（全書籍）"),
    config: Path | None = typer.Option(None, "--config", "-c", help="設定 JSON"),
    pdf: str = typer.Option("embed", "--pdf", help="原典PDFの扱い: embed（同梱）/ sidecar（隣に .pdf）/ source（原本PDFの隣に同名 .jisui、原本を参照）/ none"),
):
    """変換結果を 1ファイルの .jisui（ZIP＋マニフェスト）にまとめる。

    out/<book>.jisui を書き出す。ビューワ(Theta.Book.Hatch)がダブルクリックで開く想定。
    --pdf sidecar では .jisui の隣に <book>.pdf も置く（該当ページPDFを開く用）。
    """
    from .pack import pack_book

    cfg = _load_cfg(config)

    def _one(work_dir: Path, name: str) -> None:
        cfg.work_dir = work_dir
        try:
            r = pack_book(cfg, name, pdf_mode=pdf)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]{name}: {e}[/]")
            return
        extra = f"（隣に {Path(r['sidecar']).name}）" if r.get("sidecar") else ""
        console.print(f"[green]{name}[/] → {r['jisui']}  [PDF:{r['pdf_mode']}]{extra}")

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
