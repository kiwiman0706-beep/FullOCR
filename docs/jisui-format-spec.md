# `.jisui` コンテナ仕様 `jisui-1`

FullOCR が作った Obsidian Vault 形式の変換結果（複数の Markdown ＋ 画像 ＋ 任意で
ダイジェスト・原典PDF）を、**1ファイル `.jisui`** にまとめて持ち運び・整理しやすくする
ための形式。**Theta.Book.Hatch**（ビューワ）がダブルクリックで開く。

- 形式識別子: **`jisui-1`**
- 中身: **ZIP**（DEFLATE）。ルートに `jisui.json`（マニフェスト）＋ Vault 一式。
- 関連仕様: ページ対応 [`page-anchor-spec.md`](./page-anchor-spec.md)（`fullocr-1`）、
  ダイジェスト [`digest-spec.md`](./digest-spec.md)（`theta-digest-1`）。
- 役割: **FullOCR** が書き出し（`fullocr pack` / GUI 📦）、**Theta.Book.Hatch** が読み取り＋拡張子登録。

---

## 1. なぜ ZIP ＋ マニフェストか

`.jisui` は実体が ZIP（EPUB/docx と同じ発想）。拡張子を変えるだけで、

- 既存の unzip で中身を取り出せる（ロックインしない）
- 1ファイルで Vault 一式が完結し、整理・バックアップ・共有が楽
- マニフェストで「原典PDFはどこか」「どのスキームか」をビューワに明示できる

---

## 2. 内部レイアウト

```
<book>.jisui  (ZIP)
├── jisui.json                 # マニフェスト（必須・ルート直下）
├── 00_index.md                # エントリ（目次）
├── <章>.md …                  # 本文（1冊1ファイル、または章分割）
├── attachments/               # 挿絵・表の画像
│   └── p0007_*.png …
├── _digest.md                 # 任意: ダイジェスト（theta-digest-1）
├── _index_terms.txt           # 任意: 索引見出し語
├── _structure_review.md       # 任意: 構成レビュー
└── <book>_text.pdf            # 任意: 原典PDF（pdf.mode=embedded のとき同梱）
```

- ファイル名・フォルダ構成は `out/<book>/` をそのまま入れる（相対パスで格納）。
- MD 内部の画像・PDF リンクは Vault 相対のまま。ビューアは ZIP 内相対で解決する。

---

## 3. マニフェスト `jisui.json`

```json
{
  "format": "jisui-1",
  "title": "ねころんで読めるてんかん診療",
  "author": "中里 信和",
  "entry": "00_index.md",
  "pdf": { "mode": "embedded", "name": "ねころん_text.pdf" },
  "page_anchors": "fullocr-1",
  "digest": "theta-digest-1",
  "isbn": "9784840000000",
  "publisher": "…",
  "generator": "fullocr 0.13.1",
  "created": "2026-10-20"
}
```

| キー | 必須 | 意味 |
|---|---|---|
| `format` | ✅ | `jisui-1`。これで `.jisui` と判定。 |
| `title` / `author` | | 表示用（なければファイル名）。 |
| `entry` | ✅ | 最初に開く MD（通常 `00_index.md`）。 |
| `pdf` | ✅ | 原典PDFの所在（§4）。 |
| `page_anchors` | | 本文がページアンカー対応なら `fullocr-1`。 |
| `digest` | | `_digest.md` があれば `theta-digest-1`。 |
| `isbn` / `publisher` | | 書誌（取得済みのとき）。 |
| `generator` / `created` | | 生成元・日付。 |

---

## 4. 原典PDFの3モード（`pdf.mode`）

ユーザーが「同梱して1ファイル完結」か「`.jisui` を軽くして隣にPDF」を選べる。

| mode | 置き場所 | マニフェスト | ビューアの開き方 |
|---|---|---|---|
| `embedded` | ZIP 内 `pdf.name` | `{"mode":"embedded","name":"…_text.pdf"}` | ZIP内のそのエントリを開く |
| `sidecar` | `.jisui` と同じフォルダの `pdf.sidecar` | `{"mode":"sidecar","name":"…_text.pdf","sidecar":"<book>.pdf"}` | `<.jisui と同じ dir>/<sidecar>` を開く |
| `none` | なし | `{"mode":"none"}` | PDFは開けない（印刷ページ表示のみ） |

- `pdf.name` は **MD リンクが参照する論理名**（`[[…_text.pdf#page=N]]` の `…_text.pdf`）。
- `sidecar` の実ファイル名は `.jisui` と同じベース名＋`.pdf`（例 `ねころん.jisui` → `ねころん.pdf`）。
- ページ番号 `N` は **物理ページ（1始まり）**。`page-anchor-spec` と同一。

### ビューアのPDF解決手順
1. `pdf.mode==="embedded"` → ZIP内 `pdf.name` を開く。
2. `pdf.mode==="sidecar"` → `.jisui` と同じディレクトリの `pdf.sidecar` を探して開く。
   無ければ「PDFが見つかりません」（移動時に取り残された等）。
3. `pdf.mode==="none"` → PDFジャンプを無効化（印刷ページ番号は表示可）。

---

## 5. Theta.Book.Hatch 側の実装

### 5.1 開く
1. `.jisui` を ZIP として開き、`jisui.json` を読む（`format` 確認）。
2. `entry`（`00_index.md`）から Vault を描画。画像・章リンクは ZIP 内相対で解決。
3. 「原文を見る」→ §4 の手順でPDFを開き、`#page=N` のページへ。
4. `_digest.md` があれば `digest-spec` に従って要約/マインドマップを表示。

> ZIP はメモリ展開でもよいが、PDFが大きい `embedded` では一時フォルダへ展開して
> OS標準PDFビューア/内蔵ビューアに渡すのが楽。`sidecar` は展開不要でそのまま開ける。

### 5.2 拡張子の関連付け（ダブルクリック起動）
ビューアのインストーラが `.jisui` を登録する（FullOCR はファイルを作るだけ）。

- **Windows**: `HKCU\Software\Classes\.jisui` → ProgID、ProgID の `shell\open\command` に
  `"ThetaBookHatch.exe" "%1"`。アイコンも ProgID に設定。
- **macOS**: アプリの `Info.plist` に `CFBundleDocumentTypes`（`LSItemContentTypes` に
  自前UTI `com.theta.jisui`）＋ `UTExportedTypeDeclarations`（`.jisui` / `public.zip` 準拠）。
- **Linux**: `~/.local/share/mime` に MIME（`application/x-jisui`、glob `*.jisui`）を追加し、
  `.desktop` の `MimeType` に登録 → `update-mime-database` / `update-desktop-database`。

MIME の推奨値: `application/x-jisui`（内部は ZIP なので `application/zip` 互換）。

---

## 6. FullOCR 側（書き出し）

```
fullocr pack work/<book>/            # out/<book>.jisui（PDF同梱）
fullocr pack work/<book>/ --pdf sidecar   # .jisui を軽く＋隣に <book>.pdf
fullocr pack work/ --pdf none        # 全書籍、PDFなし
```

- GUI: 各書籍行の **📦.jisui**（同梱 or sidecar を選択）。
- 出力先は `out/`（保存先に Obsidian 保管庫を指定していればその隣）。

---

## 7. バージョニング

- 現行 `jisui-1`。破壊的変更時は `jisui-2` に上げ本書へ差分を追記。
- `-1` の「ZIP＋ルート `jisui.json`」「`pdf.mode` 3種」「`#page=N` 物理ページ」は後方互換に保つ。
