# NDLOCR-Lite v2.0 対応状況（移行メモ）

2026-11-24 に NDLOCR-Lite が **v2.0** になった。FullOCR はこれを OCR エンジンとして使う。
このメモは「何が変わり、FullOCR 側で何を直したか／まだ何が要るか」をまとめる。別スレッド
（手元に v2 を入れた環境）での検証チェックリストを兼ねる。

## v2.0 のおもな変更（FullOCR 目線）

- **PyPI 配布**：`uv tool install ndlocr-lite` / `pip install ndlocr-lite`（従来はクローン必須）
- 認識モデル刷新（レイアウト=DEIMv2、文字=PARSeq、表=TableCenterNet、backbone=DINOv3蒸留ViT-Tiny）
- **縦中横がデフォルト対応**、**異体字→新字体の包摂**、手書き精度向上
- 表構造の自動推定 → **HTML 出力**、テキストデイジー出力（アクセシビリティ）
- **モデルは初回実行時に NDL から DL（約157MB）してキャッシュ**（`python ocr.py`/GUI 版は同梱モデルを使う）
  - キャッシュ: Win `%LOCALAPPDATA%\ndlocr-lite\models\v2` / mac `~/Library/Caches/ndlocr-lite/models/v2` / Linux `${XDG_CACHE_HOME:-~/.cache}/ndlocr-lite/models/v2`

## FullOCR は v2 でそのまま動く想定（根拠）

公式リポジトリのソース確認で、FullOCR が依存する3点は v2 でも健在：

| 依存 | v2 | 根拠 |
|---|---|---|
| `--sourcepdf` で PDF 直渡し | ◯ | ocr.py に PDF 入力処理あり |
| 透明テキスト PDF `*_text.pdf` | ◯ | `embed_text_layer_pdf`（不可視テキスト描画, TextRenderMode 3） |
| 出力 JSON のキー | ◯ | 最上位 `contents`+`pages`(img_width/height/name)、行は `boundingBox`/`text`/`confidence`/`isVertical` |

→ `NDLConfig.command`（`ndlocr-lite --sourcepdf {input} --output {outdir}`）と
`pipeline/ndl.py`（`parse_ndl_json` が `boundingBox`/`confidence`/`isVertical` を読む）は
**無改修で動く見込み**。本メモの検証で確定させる。

## 別スレッドでの検証チェックリスト（手元 v2 で1冊）

```powershell
uv tool install --upgrade ndlocr-lite        # v2 へ
ndlocr-lite --version                         # 2.0.x を確認
fullocr run "<1冊.pdf>" -c fullocr.config.json
```

確認すること：
1. `work/<book>/ndl/` に **`*_text.pdf`** が出る（見出し→PDFリンク/しおり/構造エンジン入力に使う）。
   出ない場合は `NDLConfig.pdf_glob`（既定 `**/*_text.pdf`）を実ファイル名に合わせる。
2. `work/<book>/ndl/` の **JSON を `ndl.py` が読めて**、`out/<book>/` に本文が出る。
   - 特に **`contents` がページ毎の「行配列のリスト」** `[[line,...],...]` のままか？
     もし v2 で `contents` が全行フラットに変わっていたら、`parse_ndl_json` の case 0 に
     「pages 件数で分割」する分岐を足す（下記 TODO）。
3. 縦書き本で読み順が崩れていないか（v2 は縦中横デフォルト）。
4. 異体字→新字体の包摂で、検索したい語の表記が変わっていないか（索引→用語にも影響）。

結果（`ndl.py` が読めたか・`_text.pdf` が出たか・崩れが無いか）を貼ってもらえれば、
ズレがあれば `ndl.py` / `NDLConfig` を調整する。

## まだ必要な作業（配布 exe の v2 化）

- **バンドル exe のモデル同梱**：v2 はモデルを初回DL方式にしたため、`src/model` を
  そのまま同梱する現行CI（`--add-data src/model`）は v2 では成立しない。
  → 当面 **CI は NDLOCR-Lite を v1.3.1 にピン**して exe を維持（`build-*.yml`）。
  → v2 化するには、ビルド時に一度 OCR を走らせてモデルをキャッシュへ取得し、その
     キャッシュを同梱＋凍結 exe がオフラインで参照できるようキャッシュパスを設定
     （docling のモデル事前取得と同じ方式）。手元 v2 環境でキャッシュ構成を確認してから。
- `requirements.txt` 前提のCIステップも、v2（pyproject/PyPI）では `uv pip install .` 等へ要変更。

## 現状の切り分け

- **ローカル利用（手元で `fullocr run`）**：v2 でOK（pip/uv で導入、上のチェックリストで確認）。
- **配布 exe（FullOCR-Setup.exe）**：当面 v1.3.1 同梱のまま（壊さない）。v2 同梱は上記TODO後。
