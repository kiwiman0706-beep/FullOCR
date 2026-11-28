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

## 検証結果（2026-11-28、8Books 実運用スレ・Windows 11 / CPU のみ）

- 入れ方：`python -m venv ndl2 && ndl2\Scripts\pip install ndlocr-lite` → `ndlocr-lite 2.0.0`。
  **venv の普通の console script なので、v1 で起きた uv trampoline の失敗（field report 問題 1）が起きない**
- チェックリスト：
  1. `*_text.pdf` が出る … ◯
  2. `ndl.py` が v2 の JSON を読めて本文が出る … ◯（`fullocr run -p bookhatch` が rc=0、`.jisui` あり、本文 5,654 字。**無改修で動いた**）
  3. 縦書きの読み順 … 未確認（比較ページで見本ページの読み比べを利用者が確認する）
  4. 異体字の包摂 … 未確認（索引語への影響は別途）
- v1.3（同じ本の既存の結果）との比較：見本 4 冊（20〜80 ページ）

| 本 | 文字数 v1→v2 | 確信度 v1→v2 | v2 の時間 |
|---|---|---|---|
| 絵本（縦書き・31p） | 4,213 → 3,751 | 0.863 → 0.892 | 87 秒 |
| 医学雑誌（横書き・80p） | 110,018 → 110,030 | 0.899 → 0.901 | 279 秒 |
| 一般雑誌（カラー） | 44,030 → 42,730 | 0.827 → 0.833 | 187 秒 |
| 漫画の抜粋 | 70,630 → 65,370 | 0.770 → 0.809 | 205 秒 |

  文字数は全体で 0.97 倍。減った分の中身は未確認（絵・コマの中の誤検出が減った可能性）。確信度はすべて上がった
- これを受けて、8Books の残り（約 700 冊）は v2 で続行（`PATH` の先頭に `ndl2\Scripts` を置くだけ）
