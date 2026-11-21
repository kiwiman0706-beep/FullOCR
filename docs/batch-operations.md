# 大量変換の運用（夜間・分割・再開）

蔵書を全部 Markdown / `.jisui` 化するときの回し方。1回で終わらせず、毎晩少しずつ
安全に進める前提。

## 基本コマンド（8Books 向け）

```powershell
fullocr batch "D:\8Books" -p bookhatch -r
```

- `-p bookhatch` … 8Books 用プリセット（ページアンカー ON、原本の隣に `X.jisui` を書く）
- `-r` … サブフォルダも辿る（同名の本は別フォルダ扱いで衝突しない）

これは**何度流しても安全**：
- 変換済みの本はスキップ（`out/<book>/00_index.md` か、原本の隣の自分の `X.jisui` があれば済み）
- born-digital（電子生成）PDF は埋め込みテキスト層から作るので **NDL をスキップして数秒**。
  スキャン本だけ NDL（1ページ約5〜7秒）。
- 失敗した本は `out/batch_report.json` に原因付きで残る（そこだけ後追い）

## 夜間運用（区切って翌晩に再開）

一晩分だけ走らせたいとき:

```powershell
fullocr batch "D:\8Books" -p bookhatch -r --max-minutes 360   # 6時間で打ち切り
fullocr batch "D:\8Books" -p bookhatch -r --limit 50          # 50冊で打ち切り
```

- 上限に達すると**残りは手つかずで停止**。翌晩また同じコマンドを流せばスキップ機能で続きから。
- 失敗は自動リトライ（既定1回）。恒久的に落ちる本は report に残り、全体は止まらない。

### Windows タスクスケジューラ例（毎晩1時）

```
プログラム: powershell.exe
引数: -Command "$env:PYTHONUTF8=1; fullocr batch 'D:\8Books' -p bookhatch -r --max-minutes 360"
```

（`PYTHONUTF8=1` は必須。venv の `fullocr` に PATH を通すか、絶対パスで。）

## 進捗の確認

- `out/batch.log` … 1冊ごとの行（done/skipped/error）。画面には最後の要約だけ出る。
- `out/batch_report.json` … done/skipped/failed の一覧（失敗は原因つき）。

## 優先順位の付け方

- **軽い順に片付く**：born-digital が多ければ勝手に速く終わる。重いスキャン本だけ時間がかかる。
- フォルダ単位で回したいときは、そのサブフォルダを直接指定（`fullocr batch "D:\8Books\医学" -p bookhatch -r`）。

## AI 構造化を後でまとめて回す場合の費用

変換（無料）を先に全部終えてから、`fullocr estimate work\` で「全部に AI 構造化/要約を
かけるといくらか」を集計できる（課金なし）。納得できたら `fullocr digest` 等を一括で。

## まだ未対応

- **EPUB**（蔵書の最大量）は現状 FullOCR では変換しない方針（BookHatch でそのまま読む）。
- 平坦な見出し階層・飾り見出しはルールでは限界 → AI 構造化（Gemini）待ち。
