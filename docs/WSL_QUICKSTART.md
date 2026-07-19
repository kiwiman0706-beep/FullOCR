# WSL で Claude Code を使う — 操作クイックスタート

PowerShell での普段の流れ（空フォルダ作る → ファイル入れる → `cd` → `claude`）を、
WSL に置き換えるとどうなるかをまとめる。

## 大前提：フォルダの世界が2つある

WSL は「Windows の中の別の Linux PC」。ファイルの置き場所が2系統ある。

| | 場所 | WSL から見たパス | 用途 |
|---|---|---|---|
| Linux 側ホーム | Ubuntu の中 | `~`（＝`/home/ユーザー名/`）| **開発はここ**。速くてトラブルが少ない |
| Windows 側 | C ドライブ等 | `/mnt/c/...` | サンプルPDF等を**ここから持ち込む** |

橋渡し：
- **WSL → Windows のファイルを見る**：`/mnt/c/Users/<Windowsのユーザー名>/Desktop/...`
- **Windows → WSL のファイルを見る**：エクスプローラーのアドレス欄に `\\wsl$\Ubuntu\home\<ユーザー名>\` と入力（ドラッグ&ドロップ可）
- WSL のシェルで `explorer.exe .` と打つと、今いる Linux フォルダをエクスプローラーで開ける

### ★ GUI（エクスプローラー）だけでファイルをコピーしたい

コマンドを使わず、普段のフォルダ操作でコピーできる。**これが一番ラク。**

1. **アドレス欄に打ち込む**：エクスプローラー上部のアドレス欄に
   `\\wsl$\Ubuntu\home\<ユーザー名>\projects\FullOCR` を入力して Enter。
   （出ないときは `\\wsl.localhost\Ubuntu\home\<ユーザー名>\projects\FullOCR`）
   → 普通のフォルダとして開くので、`input` フォルダを作って PDF をドラッグ&ドロップ。
2. **Windows 11 なら左のペンギン**：ナビゲーションウィンドウ最下部の「Linux」→
   `Ubuntu` → `home` → `<ユーザー名>` → `projects` → `FullOCR` とたどっても同じ。
3. **シェルから一発**：`cd ~/projects/FullOCR && mkdir -p input && explorer.exe input`
   で目的フォルダがそのまま開く（パスを打たなくて済む）。

一度開いたら「クイックアクセスにピン留め」しておくと、次回からワンクリックで行ける。
ディストリ名が `Ubuntu-22.04` 等になっている場合はパスの `Ubuntu` を読み替える。

> ⚠️ プロジェクトは `~/projects/...`（Linux側）に置くこと。`/mnt/c/...` の中に置くと
> 遅く、改行コードや権限で不具合が出やすい。**入力PDFだけ /mnt/c からコピーして持ち込む**。

---

## STEP 1（Windows・1回だけ）WSL を入れる

管理者 PowerShell で1行：

```powershell
wsl --install
```

（これは同梱の `scripts/windows-wsl-install.ps1` と同じことをする。スクリプトを
使う場合はリポジトリZipを展開したフォルダで `.\scripts\windows-wsl-install.ps1`。
どこに置いてもよい＝ただの初回ブートストラップ）

- 再起動を求められたら再起動。
- その後 Ubuntu が起動し、**Linux用のユーザー名とパスワード**を作る（Windowsのとは別物でOK）。
- 以後 Ubuntu は、スタートメニューの「Ubuntu」か、Windows Terminal のタブから開ける。

---

## STEP 2（WSL・1回だけ）開発環境をまるごと構築

Ubuntu のシェルが「PowerShell の代わり」。ここで環境構築スクリプトを走らせる。
リポジトリの取り込み方は2通り。**どちらか一方**でよい。

### 方法A：普段の習慣どおり Zip を持ち込む（gitアクセス不要・確実）

1. Windows 側でリポジトリZipを展開（例：`デスクトップ\FullOCR`）
2. Ubuntu のシェルで、それを Linux 側にコピーして実行：

```bash
mkdir -p ~/projects
cp -r /mnt/c/Users/<Windowsのユーザー名>/Desktop/FullOCR ~/projects/FullOCR
cd ~/projects/FullOCR
bash scripts/setup-wsl.sh --all
```

> `setup-wsl.sh` は「今いる場所が FullOCR リポジトリ」だと気づくと clone せず
> そのまま使う。だから Zip 持ち込みでもそのまま動く。

### 方法B：リポジトリが取得可能ならワンライナー

```bash
curl -fsSL https://raw.githubusercontent.com/kiwiman0706-beep/FullOCR/claude/self-scanned-pdf-ocr-356yyx/scripts/setup-wsl.sh | bash -s -- --all
```

（スクリプトが `~/projects/FullOCR` に自動 clone してくれる。リポジトリが private の
場合は方法A を使う）

どちらも最後に `pytest` まで走って「動く状態」になる。

---

## STEP 3（毎回）作業する

PowerShell と全く同じ感覚。フォルダに `cd` して `claude` を起動するだけ。

```bash
cd ~/projects/FullOCR
claude
```

初回だけ `claude` がブラウザ認証を求める（Anthropic アカウントでログイン）。

### サンプルPDFを入れて変換してみる

```bash
mkdir -p ~/projects/FullOCR/input
# Windows 側の PDF を Linux 側へコピー
cp /mnt/c/Users/<Windowsのユーザー名>/Desktop/sample.pdf ~/projects/FullOCR/input/

cd ~/projects/FullOCR
uv run fullocr run input/sample.pdf     # NDL/Marker が入っていれば実変換
```

出力は `out/sample/`（Markdown + 挿絵）。Windows のエクスプローラーで見たいときは：

```bash
explorer.exe out/sample
```

Obsidian の保管庫に入れたいなら、その `out/sample` フォルダを保管庫にコピーするか、
`\\wsl$\Ubuntu\home\<ユーザー名>\projects\FullOCR\out\sample` を保管庫内へドラッグ。

---

## よくある迷子ポイント

- **今どこにいる？** → `pwd`（現在地表示）、`ls`（中身一覧）。PowerShell と同じ発想。
- **Windowsのユーザー名がわからない** → `ls /mnt/c/Users/` で一覧が出る。
- **PDFの置き場所がわからなくなった** → まず `/mnt/c/Users/<名前>/Desktop` や
  `/mnt/c/Users/<名前>/Downloads` を `ls` で探す。
- **Ubuntu をもう一度開くには** → スタートメニュー「Ubuntu」、または PowerShell で `wsl`。
- **VS Code で開きたい** → WSL フォルダで `code .`（WSL 拡張が自動で入り、GUIエディタで編集可）。
