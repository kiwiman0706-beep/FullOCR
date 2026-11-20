# 設定プリセットと既定値の根拠

`fullocr presets` で一覧、`--preset <名前>` で設定ファイルの上から重ねる
（`fullocr init-config -p bookhatch` / `fullocr batch in -p math` 等）。
中身は `src/fullocr/presets.py`。ここには「なぜその値か」を実本テストの数字で残す。

## プリセット

| 名前 | 用途 | 主な差分 |
|---|---|---|
| `bookhatch` | Theta Book Hatch で読む（**既定とほぼ同じ**） | 不可視ページアンカー ON、見出しの可視PDFリンク OFF、印刷目次は残す |
| `obsidian` | Obsidian で読む | アンカー ON＋見出しごとに「📄原文 p.N」リンク、印刷目次を `%%` で隠す |
| `math` | 数式の多い理工系 | Docling の数式認識（`enrich_formula`）で数式を `$$LaTeX$$` に |

## 既定値の根拠（2026-11 実本テスト）

サンプル: CKD診療ガイド（born-digital・表多）、3秒で心電図を読む本（スキャン・縦書き前付け・178p）、
ヨーロッパ企画の本（雑誌風）、寝る整体（Kindle）、化学の理論（数式多）。

### `render.page_anchors = True`（旧既定 False）
- 不可視の HTML コメントなのでアウトライン・表示を汚さない。BookHatch の「原文を見る」
  （`md-page-anchors.js`）はこれが無いと動かない。コストはほぼゼロ → 既定 ON。
- 印刷ページ番号（`print=`）の取得率: 心電図本 0/178 → **139/178**（offset 6 で全一致）。
  NDL はノンブルの信頼度を 0.0〜0.3 と出して足切りされていた／そもそも読まないページが
  多いので、Docling の page_footer テキストでも補う。

### `docling.table_mode = "fast"`（据え置き）
CLI 経路で実際に効くようにしたうえで A/B（NDL 結果は共通、Docling のみ再実行、50ページ）:

| | 時間 | 結果 |
|---|---|---|
| fast | 238 秒 | 基準 |
| accurate | 284 秒（+19%） | CKD の病期表は列が正しく揃った一方、別の表では行が潰れて悪化。化学の理論は差なし |

勝ち負けが混在し、既定の `tables_as_images=True` では表は画像で見せてテキストは検索用の
折りたたみ枠にしか入らないので、遅くする価値なし → fast のまま。

### `docling.enrich_formula = False`（`math` プリセットで ON）
化学の理論 30 ページ: 約 140 秒 → **974 秒（約7倍）**。検出された数式 7 個は
`2H2+02~→ 2H20`（NDL）→ `2 \, H _ { 2 } + O _ { 2 } \longrightarrow 2 \, H _ { 2 } O`
のように大幅に改善。ただし日本語の混じる式（`H2(気)`）の「気」が記号に化けたり、
式の末尾に余計な項が付くことがある。全冊既定にするには遅すぎるので opt-in。

### `docling.images_scale`
CLI に対応フラグが無く、CLI 経路では効かない（ページ画像は FullOCR 側が
`page_render_dpi` で描く）。exe（in-process）経路のみ有効。未評価。

## 未評価（今後の A/B 候補）
- `page_render_dpi`（300 → 200 で図の切り出し画質と速度）
- `ndl.min_confidence`（0.5）
- `hide_toc_pages`（AI 構造化を使うなら ON が妥当）
- `structure_llm.*`（Gemini キー待ち）
