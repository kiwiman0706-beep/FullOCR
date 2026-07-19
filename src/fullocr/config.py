"""パイプライン設定。

実行環境依存の値（NDLOCR-Lite / Marker の場所や起動方法）はここに集約し、
将来 exe へ同梱する際にここだけ差し替えれば済むようにする。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class NDLConfig(BaseModel):
    """NDLOCR-Lite の呼び出し設定。

    exe 同梱前は、ユーザー環境にインストール済みの NDLOCR-Lite CLI を叩く。
    `command` は {input} と {outdir} を含むテンプレート。実バイナリの
    引数仕様に合わせてここを調整する（README 参照）。
    """

    enabled: bool = True
    # 実機で確認済み（`uv tool install .` で入る `ndlocr-lite` コマンド）。
    # --sourcepdf は自動で <stem>.json と <stem>_text.pdf（透明テキストPDF）を {outdir} に書く。
    command: str = "ndlocr-lite --sourcepdf {input} --output {outdir}"
    # NDL が JSON を書き出すサブパス（{outdir} 起点）。
    json_glob: str = "**/*.json"
    # 透明テキストPDF の書き出し名パターン。
    pdf_glob: str = "**/*_text.pdf"
    # この信頼度未満の行は merge 時に捨てる（実本検証で見つかった、装飾文字や
    # 小さすぎる領域での誤認識・幻覚混入の対策。confidence が無い行は素通しする）。
    min_confidence: float = 0.5


class MarkerConfig(BaseModel):
    """Marker の呼び出し設定。

    Marker は「Markdown を吐く箱」ではなく「ブロック種別・図領域・読み順を
    JSON で出す分類器」として使う（--output_format json）。テキストは NDL 由来を
    優先し、Marker からは構造のみ採用する。
    """

    enabled: bool = True
    # 例: "marker_single {input} --output_format json --output_dir {outdir}"
    command: str = "marker_single {input} --output_format json --output_dir {outdir}"
    json_glob: str = "**/*.json"
    # Marker 自前 OCR を無効化し NDL テキスト層を尊重するか（コマンド側で指定する想定）。
    disable_ocr: bool = True


class DoclingConfig(BaseModel):
    """Docling の呼び出し設定（推奨エンジン）。

    NDL の透明テキストPDF を Docling に渡し、Docling 側 OCR を切ると NDL の
    テキスト層をそのまま使える。構造(表/見出し/読み順)は Docling が担当。
    command は JSON 出力を吐くように調整する（例では docling CLI）。
    """

    enabled: bool = True
    # 実機で確認済み（`uv pip install -e ".[docling]"` で入る `docling` CLI, v2.x）。
    # サブコマンドは `convert`（`docling <file>` の省略形も動くが明示する）。
    command: str = "docling convert {input} --to json --output {outdir} --no-ocr"
    json_glob: str = "**/*.json"


class MinerUConfig(BaseModel):
    """MinerU の呼び出し設定（数式・表が濃い専門書向けの代替）。

    middle.json から構造を採り、テキストは NDL を主に座標マージする。
    """

    enabled: bool = True
    # 例: "mineru -p {input} -o {outdir}"
    command: str = "mineru -p {input} -o {outdir}"
    json_glob: str = "**/*middle.json"


class RenderConfig(BaseModel):
    """Markdown / Obsidian 出力設定。"""

    # 画像リンク記法。Obsidian の場合 wiki 記法 ![[...]] が扱いやすい。
    obsidian_wiklinks: bool = True
    images_dirname: str = "attachments"
    # 章（H1/H2）ごとにファイル分割するか。既定は分割なし（1冊1ファイル）。
    # Docling/MinerU は実データで見出しレベルを一律にしか返さないことが確認されて
    # おり（README/CLAUDE.md参照）、1 にすると章タイトルだけでなく大半の見出しで
    # 分割されてしまう（実本検証で数百ファイルに過剰分割される事例を確認済み）。
    split_by_heading_level: int | None = None
    # front-matter を付けるか。
    front_matter: bool = True
    # ページ上部/下部のヘッダ・フッタ（柱・ノンブル）を本文から除外する。
    drop_headers_footers: bool = True


class PipelineConfig(BaseModel):
    # 構造解析エンジンの選択。docling(推奨) / mineru(数式・表向き) / marker。
    engine: str = "docling"

    ndl: NDLConfig = Field(default_factory=NDLConfig)
    docling: DoclingConfig = Field(default_factory=DoclingConfig)
    mineru: MinerUConfig = Field(default_factory=MinerUConfig)
    marker: MarkerConfig = Field(default_factory=MarkerConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)

    # 作業ディレクトリ（中間生成物）。
    work_dir: Path = Path("work")
    # 最終出力ディレクトリ。
    out_dir: Path = Path("out")
    # マージ時、NDL 行を Marker ブロックへ割り当てる最小オーバーラップ率。
    assign_overlap_threshold: float = 0.3

    @classmethod
    def load(cls, path: str | Path) -> "PipelineConfig":
        import json

        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")
