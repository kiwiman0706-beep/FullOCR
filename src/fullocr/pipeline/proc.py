"""サブプロセス実行の共通ヘルパ（出力を1行ずつライブに流す）。

NDLOCR-Lite / Docling(CLI) は処理中に標準出力へ進捗を出す。GUI では「止まって
いるのか動いているのか」を見せたいので、`subprocess.run(capture_output=True)` の
ように最後にまとめて受け取るのではなく、`Popen` で1行来るたびに `on_output` に
渡す（=コンソールへライブ表示できる）。同時に末尾数百行を保持し、失敗時のエラー
メッセージに載せる。
"""

from __future__ import annotations

import subprocess
from collections import deque
from collections.abc import Callable


def run_streaming(
    cmd: str,
    on_output: Callable[[str], None] | None = None,
    *,
    tail_lines: int = 200,
) -> tuple[int, str]:
    """shell コマンドを実行し、stdout+stderr を1行ずつ on_output に流す。

    戻り値は (returncode, tail_text)。tail_text は末尾 tail_lines 行の連結で、
    呼び出し側が失敗時のエラーメッセージに使う。
    """
    tail: deque[str] = deque(maxlen=tail_lines)
    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # stderr も stdout にまとめて行順を保つ
        # エンジン(NDL/Docling)の出力は UTF-8。Windows の既定(cp932)で読むと日本語
        # ログで UnicodeDecodeError になり変換自体が落ちるため、明示的に UTF-8 で読む。
        encoding="utf-8",
        errors="replace",
        bufsize=1,  # 行バッファ（ライブ表示のため）
    )
    if proc.stdout is not None:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            tail.append(line)
            if on_output is not None and line.strip():
                on_output(line)
    proc.wait()
    return proc.returncode, "\n".join(tail)
