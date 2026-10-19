# Theta.FullOCR

自炊した挿絵付きの専門書PDFを、**文書構造・参考画像・目次つきの Markdown** に変換し、
Obsidian などでそのまま読めるようにする個人用 AI-OCR バッチ／GUI ツール。

認識精度の高い **NDLOCR-Lite**（日本語・縦書きに強い / GPU不要）でテキスト化し、
**構造解析エンジン**（**Docling** 推奨 / MinerU / Marker から選択）のレイアウト解析
（見出し階層・図領域・表・読み順）を組み合わせて、「高精度テキスト × きれいな構造」を
両取りするのが狙い。

> ステータス: **フェーズ1（コア・パイプライン骨組み）実装中**。
> NDL/構造エンジンの実行部はユーザー環境（Windows・CPU）で動く前提で、
> パース〜マージ〜Markdown生成は合成データでテスト済み（Docling/MinerU/Marker 対応）。

---

## 設計の中心：IR（中間表現）

すべての工程は座標付きの中間表現 `fullocr.ir.Document` を介してつながる。

```
入力PDF（自炊・画像のみ）
  │
  ├─[1] NDLOCR-Lite   → 透明テキストPDF + JSON(座標付きテキスト)   ※GPU不要・公式ツール
  │
  ├─[2] 構造エンジン  → ブロック種別 / 図領域 / 表 / 読み順（テキストは使わない）
  │      Docling/MinerU/Marker（JSON）
  │
  ├─[3] merge         → IR（NDLの高精度テキスト × エンジンの構造）
  │                         └─ [任意] GUIで補正（読み順・種別・誤字）※フェーズ3
  │
  └─[4] render        → Obsidian用 Markdown + 挿絵(attachments/) + 目次
```

**なぜ IR を挟むのか**：GUI でレイアウト認識を直したい（e.Typist のような使い勝手）場合、
補正は「最終 Markdown」ではなく **座標付きブロックの段階** で行う必要がある。
構造エンジンを「Markdown を吐く箱」ではなく「**構造を JSON で出す分類器**」として使い、
IR に対して補正 → Markdown 再生成、という流れにすることで GUI を後付けできる。
IR がエンジン非依存なので、**Docling / MinerU / Marker を同じ土台で差し替え・比較**できる。

---

## 構造エンジンの選択（`engine`）

文字認識は NDLOCR-Lite 固定。その相棒（レイアウト解析）を `config.engine` で選ぶ。

| engine | 役割・強み | 向き | NDL協業 |
|---|---|---|---|
| **`docling`**（既定・推奨） | 表(TableFormer)・見出し階層・読み順が綺麗。軽くて速い。Apache-2.0 | 一般の専門書・Obsidian構造化 | ◎ 公式OCR差し替え/透明PDFのテキスト層をそのまま利用 |
| **`mineru`** | CJKレイアウト検出が最強、**数式→LaTeX** が強力 | 数式・表が濃い理工書 | ○ 座標マージ（CPUは重い） |
| **`marker`** | 軽量・Markdown生成が素直 | 英語主体・軽く回したい | ○ 既存テキスト利用 |

推奨は **`docling`**。数式・表が多い本だけ `mineru` を試す運用。実本1章で3つを
A/B比較して選ぶのが確実（IR が共通なので比較が容易）。

| | 役割 | 補足 |
|---|---|---|
| **NDLOCR-Lite** | 文字認識（真実のテキスト） | GPU不要・CPUのみ・Windows対応。TXT/JSON/**透明テキストPDF**を出力。縦書き・日本語に強い |
| **構造エンジン** | レイアウト解析（構造） | 見出し階層・**挿絵の領域切り出し**・表・読み順。Docling は CPU で軽快 |

自炊本の「挿絵」は PDF に埋め込まれた画像ではなくページ画像の一部なので、
**図領域を検出して切り出す**工程が必要。これは構造エンジン（Docling/MinerU/Marker の
レイアウト解析）の得意分野で、検出した図領域をページ画像から切り出して埋め込む。

---

## セットアップ

### Windows ユーザー向け（推奨: WSL）

このプロジェクトは OCR/ML 系ツール（NDLOCR-Lite・Marker・PyMuPDF）を使うため、
ネイティブ PowerShell より **WSL2(Ubuntu) の方が圧倒的に安定** する。Claude Code の
利用環境ごと一括で整えるスクリプトを同梱している。

```powershell
# 1) Windows の管理者 PowerShell で1度だけ（WSL2 + Ubuntu を導入）
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\windows-wsl-install.ps1
```
```bash
# 2) Ubuntu(WSL) のシェルで（Claude Code + uv + 本プロジェクト一式）
bash scripts/setup-wsl.sh          # 基本一式
bash scripts/setup-wsl.sh --all    # Marker / NDLOCR-Lite も含める（重い）
```

スクリプトは冪等（何度実行してもよい）。完了後 `claude` で認証すれば、ローカルの
Claude Code で実データ結線（フェーズ1.5）から作業を続けられる。

### 手動インストール（開発）

```bash
uv venv
uv pip install -e ".[dev]"          # コア + テスト
# 実変換を行う環境では追加で:
uv pip install -e ".[docling,pdf]"   # 推奨エンジン Docling + 図切り出し(PyMuPDF)
# 代替エンジンを試すとき: uv pip install -e ".[mineru]" / ".[marker]"
# NDLOCR-Lite は公式配布物を別途セットアップ（下記）
```

### NDLOCR-Lite の準備（ユーザー環境）
- 公式: [ndl-lab/ndlocr-lite](https://github.com/ndl-lab/ndlocr-lite)（GPU不要）
- Windows は GUI (`ndlocr_lite_gui.exe`) もあるが、バッチ自動化では CLI を使う。
- 出力に **JSON（座標）** と **透明テキストPDF** を含めるよう設定する。
- `fullocr.config.NDLConfig.command` を、実際の CLI 引数に合わせて調整すること。

> ⚠️ NDL-Lite の JSON キー名はバージョンで揺れうる。まず 1 ページ分の JSON を
> `parse_ndl_json` に通して、`fullocr/pipeline/ndl.py` の `_*_KEYS` を実出力に合わせるのが
> フェーズ0の最初の作業。

---

## 使い方（CLI）

```bash
# 設定雛形を作る（engine と NDL/エンジンのコマンド行をここで調整）
fullocr init-config fullocr.config.json

# 1冊を変換
fullocr run book.pdf -c fullocr.config.json

# フォルダ一括（処理済みはスキップ・失敗はリトライ）
fullocr run ./books --folder -c fullocr.config.json

# OCRは再実行せず、既存の作業JSONから再マージ・再生成
fullocr run book.pdf --no-engines

# GUI補正後などの IR json から Markdown を作り直す
fullocr render work/book/ir.json -c fullocr.config.json
```

出力（`out/<book>/`）:
```
00_index.md        タイトル + 目次（Obsidian wikilink）
01_<章>.md …       章ごとに分割された本文
attachments/       切り出した挿絵
```

---

## ロードマップ（最終形：全部入り exe GUI）

- [x] **フェーズ0**：開発環境確定（Python3.11 + uv / Windows・CPU）
- [x] **フェーズ1**：コア骨組み（IR / NDL・Docling・MinerU・Marker アダプタ / マージ / Markdown生成 / バッチCLI / エンジン差し替え）
- [ ] **フェーズ1.5**：実 NDL-Lite・構造エンジンとの結線（JSONキー確定、透明PDF→ページ描画→図切り出し、実本1章で3エンジンA/B）
- [ ] **フェーズ2**：Obsidian最適化強化（front-matter・章分割・脚注/表・ノンブル除去の精緻化）
- [ ] **フェーズ3**：ローカル Web GUI（ページ画像にボックス重畳、読み順の並べ替え・種別変更・誤字修正 → IR保存 → 再生成）
- [ ] **フェーズ4**：**Windows / Mac 化・一括セットアップ**
      - NDLOCR-Lite / 構造エンジン（モデル込み）を同梱し、初回起動でセットアップ完結
      - PyInstaller で単一配布物化、GUI から入力フォルダを選ぶだけで変換
      - 配布はワンクリックの**インストーラ**：Windows は Inno Setup 製 `FullOCR-Setup.exe`
        （同一 AppId なので新版を入れると**上書きで更新**）、Mac は `FullOCR.dmg`
        （Applications へドラッグ、上書きで更新）。GitHub Actions が両方ビルドする
        （`.github/workflows/build-windows.yml` / `build-macos.yml`）。
        出力（`out/` `work/`）は書込可能なユーザーのホーム配下 `~/FullOCR` に置く。

### 最終形のアーキテクチャ方針
- GUI は `pywebview`（＋FastAPI）でネイティブっぽいローカルアプリにし、
  中身は本コアパッケージをそのまま呼ぶ（CLI と GUI で同じパイプラインを共有）。
- exe 同梱時は `fullocr.config` の `command`/パスを同梱バイナリ向けに差し替えるだけで済む
  よう、実行部をアダプタに閉じ込めてある。

---

## 開発

```bash
uv run pytest -q          # パース→マージ→レンダリングを合成データで検証
uv run ruff check .
```

## ライセンス
MIT（NDLOCR-Lite・Docling・MinerU・Marker それぞれのライセンスは各配布元に従う。
特に MinerU はライセンス条件を要確認）
