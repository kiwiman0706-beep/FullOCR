#!/usr/bin/env bash
# =============================================================================
# WSL(Ubuntu) 上に Claude Code + FullOCR 開発環境を一括セットアップするスクリプト。
#
#   - apt の基本開発ツール（git / build-essential / poppler など）
#   - Node.js(LTS)  … nvm 経由（sudo 不要・権限問題を避ける）
#   - Claude Code   … npm -g @anthropic-ai/claude-code
#   - uv            … Python パッケージ/仮想環境マネージャ
#   - FullOCR       … このリポジトリを clone して venv 構築・テスト実行
#
# 使い方（Ubuntu のシェルで）:
#   bash setup-wsl.sh                 # 基本一式 + FullOCR の dev 環境
#   bash setup-wsl.sh --with-marker   # 追加で Marker(marker-pdf) も入れる（重い）
#   bash setup-wsl.sh --with-ndl      # 追加で NDLOCR-Lite を clone & セットアップ
#   bash setup-wsl.sh --all           # 上記すべて
#
# 冪等性: 何度実行してもよい（入っているものはスキップ）。
# =============================================================================
set -euo pipefail

# ---- 設定 -------------------------------------------------------------------
REPO_URL="${FULLOCR_REPO_URL:-https://github.com/kiwiman0706-beep/FullOCR.git}"
REPO_BRANCH="${FULLOCR_BRANCH:-claude/self-scanned-pdf-ocr-356yyx}"
PROJECTS_DIR="${PROJECTS_DIR:-$HOME/projects}"
NODE_VERSION="${NODE_VERSION:---lts}"

WITH_MARKER=0
WITH_NDL=0
for arg in "$@"; do
  case "$arg" in
    --with-marker) WITH_MARKER=1 ;;
    --with-ndl)    WITH_NDL=1 ;;
    --all)         WITH_MARKER=1; WITH_NDL=1 ;;
    -h|--help)     grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "不明な引数: $arg" >&2; exit 1 ;;
  esac
done

log()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }

# ---- 0. WSL/Ubuntu チェック --------------------------------------------------
if ! grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
  warn "WSL 上での実行を想定したスクリプトです（続行はします）。"
fi
if ! command -v apt-get >/dev/null 2>&1; then
  echo "apt が見つかりません。Ubuntu/Debian 系で実行してください。" >&2
  exit 1
fi

# ---- 1. apt 基本パッケージ ----------------------------------------------------
log "apt パッケージを更新・インストールします（sudo が必要です）"
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  git curl ca-certificates build-essential \
  python3 python3-venv python3-dev \
  poppler-utils \
  libgl1 libglib2.0-0 \
  unzip
ok "apt パッケージ準備完了"

# ---- 2. Node.js (nvm 経由) ---------------------------------------------------
export NVM_DIR="$HOME/.nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  log "nvm をインストールします"
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
else
  ok "nvm は導入済み"
fi
# shellcheck disable=SC1090
. "$NVM_DIR/nvm.sh"

if ! command -v node >/dev/null 2>&1; then
  log "Node.js ($NODE_VERSION) をインストールします"
  nvm install "$NODE_VERSION"
  nvm alias default "$NODE_VERSION" || true
fi
ok "Node.js $(node -v) / npm $(npm -v)"

# ---- 3. Claude Code ----------------------------------------------------------
if ! command -v claude >/dev/null 2>&1; then
  log "Claude Code をインストールします (npm -g @anthropic-ai/claude-code)"
  npm install -g @anthropic-ai/claude-code
else
  log "Claude Code は導入済み。最新へ更新します"
  npm update -g @anthropic-ai/claude-code || warn "更新スキップ"
fi
ok "Claude Code: $(claude --version 2>/dev/null || echo 'installed')"

# ---- 4. uv (Python 環境マネージャ) -------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  log "uv をインストールします"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv: $(uv --version 2>/dev/null || echo 'installed')"

# ---- 5. FullOCR リポジトリ ---------------------------------------------------
# すでにリポジトリ内で実行された場合はそれを使う。そうでなければ clone。
if [ -f "pyproject.toml" ] && grep -q 'name = "fullocr"' pyproject.toml 2>/dev/null; then
  REPO_DIR="$(pwd)"
  log "カレントが FullOCR リポジトリのため clone をスキップ: $REPO_DIR"
else
  mkdir -p "$PROJECTS_DIR"
  REPO_DIR="$PROJECTS_DIR/FullOCR"
  if [ -d "$REPO_DIR/.git" ]; then
    log "既存リポジトリを更新: $REPO_DIR"
    git -C "$REPO_DIR" fetch origin "$REPO_BRANCH"
    git -C "$REPO_DIR" checkout "$REPO_BRANCH"
    git -C "$REPO_DIR" pull --ff-only origin "$REPO_BRANCH" || warn "pull スキップ"
  else
    log "FullOCR を clone: $REPO_URL ($REPO_BRANCH)"
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR"
  fi
fi

# ---- 6. Python 仮想環境 + 依存 -----------------------------------------------
cd "$REPO_DIR"
export PATH="$HOME/.local/bin:$PATH"
log "Python 仮想環境を作成し依存をインストールします"
uv venv
EXTRAS="dev"
[ "$WITH_MARKER" -eq 1 ] && EXTRAS="${EXTRAS},marker"
uv pip install -e ".[${EXTRAS}]"
# 図切り出し・ページ描画に使う PyMuPDF（重依存でないので既定で入れておく）
uv pip install pymupdf || warn "pymupdf のインストールに失敗（図切り出しは後で対応可）"
ok "依存インストール完了 (extras: ${EXTRAS})"

log "テストを実行します"
if uv run pytest -q; then
  ok "テスト成功"
else
  warn "テストが失敗しました。ログを確認してください。"
fi

# ---- 7. (任意) NDLOCR-Lite ---------------------------------------------------
if [ "$WITH_NDL" -eq 1 ]; then
  NDL_DIR="$PROJECTS_DIR/ndlocr-lite"
  if [ -d "$NDL_DIR/.git" ]; then
    ok "NDLOCR-Lite は取得済み: $NDL_DIR"
  else
    log "NDLOCR-Lite を clone します: $NDL_DIR"
    git clone https://github.com/ndl-lab/ndlocr-lite.git "$NDL_DIR" \
      && ok "clone 完了。$NDL_DIR/README を見てモデル取得・依存導入を行ってください" \
      || warn "NDLOCR-Lite の clone に失敗。手動で取得してください"
  fi
fi

# ---- 完了メッセージ ----------------------------------------------------------
cat <<EOF

$(printf '\033[32m======================================================\033[0m')
 セットアップ完了 🎉
$(printf '\033[32m======================================================\033[0m')

 リポジトリ: $REPO_DIR

 次の一歩:
   1) 新しいシェルを開く（または: source ~/.bashrc）
   2) 認証:   cd "$REPO_DIR" && claude
              → 初回はブラウザで Anthropic アカウント認証
   3) 動作確認:
        uv run pytest -q
        uv run fullocr --help

 実データ結線（フェーズ1.5）を Claude Code に任せる例:
   claude
   > 実PDFを1ページ NDLOCR-Lite にかけて、出力JSONに合わせて
   > src/fullocr/pipeline/ndl.py のキーマッピングを直して。

 Marker/NDL を入れていない場合は再実行:
   bash scripts/setup-wsl.sh --all
EOF
