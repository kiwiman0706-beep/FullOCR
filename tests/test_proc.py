"""run_streaming（サブプロセスのライブ出力）の検証。

実バイナリ不要。/bin/sh 経由の簡単なコマンドで、1行ずつコールバックに届くこと・
失敗時に末尾を返すことを確認する。
"""

from __future__ import annotations

from fullocr.pipeline.proc import run_streaming


def test_streams_lines_live_and_returns_zero():
    lines: list[str] = []
    rc, tail = run_streaming('printf "a\\nb\\nc\\n"', on_output=lines.append)
    assert rc == 0
    assert lines == ["a", "b", "c"]  # 1行ずつ順番に届く
    assert "c" in tail


def test_captures_stderr_and_nonzero_exit():
    rc, tail = run_streaming("echo oops 1>&2; exit 3")
    assert rc == 3
    assert "oops" in tail  # stderr も stdout にまとめて捕捉


def test_blank_lines_are_not_forwarded():
    lines: list[str] = []
    run_streaming('printf "x\\n\\n\\ny\\n"', on_output=lines.append)
    assert lines == ["x", "y"]  # 空行はコールバックに流さない


def test_utf8_output_is_decoded_not_crashing():
    # エンジンは UTF-8 の日本語ログを吐く。Windows 既定(cp932)で読むと落ちるので
    # UTF-8 固定で読めていること（＝日本語がそのまま届く）を確認する。
    lines: list[str] = []
    rc, tail = run_streaming(r'printf "\346\227\245\346\234\254\n"', on_output=lines.append)
    assert rc == 0
    assert lines == ["日本"]
