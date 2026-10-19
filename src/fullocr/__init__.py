"""FullOCR: 自炊PDF → 高精度Markdown 変換パイプライン。

設計の中心は IR（中間表現, fullocr.ir）。
    NDL-Lite ─(テキスト+座標)─┐
                              ├─▶ IR ─(任意: GUI補正)─▶ Markdown + images
    Marker ──(種別/図/読み順)─┘
"""

# ビルド時、リリース版ではワークフローが release_version でこの値を上書きする
# （GUI 画面にこのバージョンを表示する）。開発中はこの既定値のまま。
__version__ = "0.12.0"

# 画面・インストーラに出す製品表示名（パッケージ名 fullocr / CLI は据え置き）。
APP_NAME = "Theta.FullOCR"
# 自動更新チェックの参照先リポジトリ（owner/repo）。
GITHUB_REPO = "kiwiman0706-beep/FullOCR"
