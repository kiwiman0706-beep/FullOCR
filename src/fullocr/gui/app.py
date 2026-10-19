"""GUI 起動エントリポイント。

ローカルで FastAPI サーバを立ち上げ、pywebview でネイティブウィンドウとして開く。
exe 化時（PyInstaller）もこのまま呼ばれる想定。

pywebview の js_api を通じて、フロントエンドから「フォルダ選択ダイアログ」と
「フォルダを開く」を呼べるようにしている（ブラウザ単体ではローカルパスを扱えない
ため、ネイティブ側に橋渡しする）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import uvicorn
import webview

from .. import APP_NAME
from .server import app

HOST = "127.0.0.1"
PORT = 8756


class Api:
    """フロントエンド(JS)から呼べるネイティブ機能。"""

    def pick_folder(self) -> str | None:
        """フォルダ選択ダイアログを開き、選ばれた絶対パスを返す。"""
        windows = webview.windows
        if not windows:
            return None
        result = windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    def open_path(self, path: str) -> bool:
        """指定パスをOSのファイルマネージャで開く。

        フォルダならそのまま開く。ファイル（ログ等）なら、可能なら選択状態にして
        親フォルダを開く（Win: explorer /select、mac: open -R、Linux: 親を開く）。
        """
        if not path or not os.path.exists(path):
            return False
        is_dir = os.path.isdir(path)
        try:
            if sys.platform.startswith("win"):
                if is_dir:
                    os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
                else:
                    subprocess.run(["explorer", "/select,", path], check=False)  # noqa: S607
            elif sys.platform == "darwin":
                subprocess.run(["open"] + ([] if is_dir else ["-R"]) + [path], check=False)
            else:
                subprocess.run(["xdg-open", path if is_dir else os.path.dirname(path)], check=False)
            return True
        except OSError:
            return False


def _run_server() -> None:
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def main() -> None:
    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()
    webview.create_window(
        APP_NAME,
        f"http://{HOST}:{PORT}",
        width=820,
        height=900,
        min_size=(560, 600),
        js_api=Api(),
    )
    webview.start()


if __name__ == "__main__":
    main()
