"""shell=True のコマンド文字列に埋め込むパスのクォート。

`shlex.quote` は POSIX シェル前提（単一引用符でエスケープ）で、Windows の
`cmd.exe`（`subprocess.run(..., shell=True)` が Windows で使うシェル）はこの
記法を理解しない。自炊ファイル名は空白・記号を含みがちなので、プラットフォーム
ごとに正しいクォートを行う。
"""

from __future__ import annotations

import shlex
import sys


def quote_path(path: str) -> str:
    if sys.platform == "win32":
        # cmd.exe は二重引用符でのエスケープを理解する。パス中の `"` はほぼ
        # 現れないが、念のため二重化しておく（cmd.exe の一般的な回避策）。
        return '"' + path.replace('"', '""') + '"'
    return shlex.quote(path)
