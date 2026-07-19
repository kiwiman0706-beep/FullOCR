"""GUI 起動エントリポイント。

ローカルで FastAPI サーバを立ち上げ、pywebview でネイティブウィンドウとして開く。
exe 化時（PyInstaller）もこのまま呼ばれる想定。
"""

from __future__ import annotations

import threading

import uvicorn
import webview

from .server import app

HOST = "127.0.0.1"
PORT = 8756


def _run_server() -> None:
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def main() -> None:
    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()
    webview.create_window("FullOCR", f"http://{HOST}:{PORT}", width=820, height=900, min_size=(560, 600))
    webview.start()


if __name__ == "__main__":
    main()
