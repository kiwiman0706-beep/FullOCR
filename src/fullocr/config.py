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
    # --- 精度ノブ（凍結時の in-process 実行に効く。既定は据え置き。PDCAで比較する用） ---
    # Docling がモデルに渡すページ画像の倍率。上げると検出精度が上がる可能性（速度低下）。
    images_scale: float = 1.0
    # 表構造(TableFormer)を解析するか。表を画像扱いにするなら不要にもできる。
    do_table_structure: bool = True
    # 表構造の精度モード: "fast" / "accurate"。
    table_mode: str = "fast"


class MinerUConfig(BaseModel):
    """MinerU の呼び出し設定（数式・表が濃い専門書向けの代替）。

    middle.json から構造を採り、テキストは NDL を主に座標マージする。
    """

    enabled: bool = True
    # 例: "mineru -p {input} -o {outdir}"
    command: str = "mineru -p {input} -o {outdir}"
    json_glob: str = "**/*middle.json"


class StructureLLMConfig(BaseModel):
    """LLM による構造化パス（見出し階層の付与＋構成の評価）。

    Markdown 化の主目的である「目次・構造化」の品質を上げるための任意ステップ。
    Docling/MinerU は見出しレベルを一律にしか返さない（＝アウトラインが平坦）ので、
    LLM に見出し列＋文脈を渡して H1〜H6 の階層を割り当て、誤検出見出しは本文へ降格し、
    さらに「本の実目次と一致しているか／Markdown構成として適切か」を評価レポートにする。

    プロバイダは差し替え可能（provider）。APIキーは環境変数 or GUI 入力（api_key）。
    キーが無ければパス全体を自動スキップ（＝オフでも従来通り動く）。
    """

    enabled: bool = False
    # 見出し階層を LLM で付与し直すか。
    assign_hierarchy: bool = True
    # 構成の評価レポート(_structure_review.md)を出すか。
    review: bool = True
    # プロバイダ（今は gemini。将来 openai / anthropic 等に差し替え可能）。
    provider: str = "gemini"
    model: str = "gemini-2.5-flash"
    # API キーを読む環境変数名。未設定時は api_key を直接使う（GUI 入力用）。
    api_key_env: str = "GEMINI_API_KEY"
    api_key: str = ""
    # 精度向上のため巻頭テキスト（印刷された目次が載っていることが多い）を参照に渡す。
    reference_head_chars: int = 8000
    # 各見出し直後の本文スニペットの長さ（見出しか否かの判断材料）。
    snippet_chars: int = 60
    # --- LLM 後処理（いずれもトークンを消費。既定オフ） ---
    # 本文の異常検出レポート（変な改行・ページ飛び等）を _structure_review.md に追記。
    anomaly_check: bool = False
    # 異常検出で全文を送る（False: ページ境界の前後だけ＝安い / True: 全文＝高精度）。
    anomaly_full_text: bool = False
    # 本文の改行・OCRゆれを LLM で整える（構造・画像は保持。段落ブロック単位で書き換え）。
    rewrite_text: bool = False
    # 挿絵の代替テキスト(alt)をマルチモーダルで生成（検索性向上。画像トークン消費）。
    figure_alt_text: bool = False
    # 見出し階層付けの際、印刷目次ページ(DocMeta.toc_pages)の「画像そのもの」を
    # マルチモーダルLLMに読ませ、正式な目次テキストを参照にする（OCR誤りに強い）。
    toc_image_reference: bool = False


class CostConfig(BaseModel):
    """AI処理のコスト概算に使う単価（GUI表示用）。

    既定は gemini-2.5-flash（$0.30/1M 入力, $2.50/1M 出力）。あくまで概算表示なので、
    為替やトークン換算はざっくり。必要なら config でここだけ差し替える。
    """

    usd_jpy: float = 155.0
    price_in_per_mtok: float = 0.30   # 入力 100万トークンあたり USD
    price_out_per_mtok: float = 2.50  # 出力 100万トークンあたり USD
    # 日本語のおおよその「1トークンあたり文字数」。概算なので粗くてよい。
    chars_per_token: float = 1.8


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
    # 表を「画像」として出す（構造が崩れると意味が変わるため原形を保つ）。中の
    # OCRテキストは画像の下に検索用キャプションとして添える。off なら Markdown 表。
    tables_as_images: bool = True
    # 印刷された「目次」ページ（DocMeta.toc_pages）を本文でコメントアウトして隠す。
    # 生成アウトライン(#見出し/Obsidian Outline)があれば印刷目次は冗長なため。
    hide_toc_pages: bool = False
    # 各見出しに、同梱の透明テキストPDFの該当ページへのリンク（Obsidian の
    # [[book_text.pdf#page=N]]）を付ける。スマホで「原文のそのページ」を開ける。
    pdf_page_links: bool = False
    # 各ページ境界に、見えない HTML コメントのページアンカーを埋める（アウトライン
    # を汚さない）。外部リーダー(dsbooks 等)が「MD の今の位置 → 原PDFの該当ページ」
    # へ切り替えるための機械可読マーカー。仕様: docs/page-anchor-spec.md。
    page_anchors: bool = False


class PipelineConfig(BaseModel):
    # 構造解析エンジンの選択。docling(推奨) / mineru(数式・表向き) / marker。
    engine: str = "docling"

    ndl: NDLConfig = Field(default_factory=NDLConfig)
    docling: DoclingConfig = Field(default_factory=DoclingConfig)
    mineru: MinerUConfig = Field(default_factory=MinerUConfig)
    marker: MarkerConfig = Field(default_factory=MarkerConfig)
    structure_llm: StructureLLMConfig = Field(default_factory=StructureLLMConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    cost: CostConfig = Field(default_factory=CostConfig)

    # ページ画像の描画DPI（図の切り出し・目次画像LLMの読み取りに効く）。速度不問なら高め。
    page_render_dpi: int = 300
    # ルールベースの本文整形（ページ境界の改行連結・ノンブル残骸除去など）。無料・既定ON。
    text_cleanup: bool = True
    # NDL の透明テキストPDF に、認識した見出しから PDF のしおり(アウトライン)を付ける。
    pdf_bookmarks: bool = True
    # ファイル名から書誌メタ（ISBN・出版社・刊行年）をネットで取得して front-matter に入れる。
    # ネットワークを使うため既定OFF（希望時のみ）。事実はDB由来（LLMに作らせない）。
    fetch_biblio: bool = False

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
