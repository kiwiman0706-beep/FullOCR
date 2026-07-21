"""JobManager の制御ロジック検証（一時停止・中止・スキップ・並べ替え）。

ワーカースレッドを使うのでイベント同期で待つ。実処理は入れ替え可能な runner に
差し替えて高速に回す。
"""

from __future__ import annotations

import threading
import time

from fullocr.gui.jobs import JobManager


def _wait_idle(m: JobManager, timeout: float = 3.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        st = m.status()
        if st["pending"] == 0 and st["current"] is None:
            return
        time.sleep(0.02)
    raise AssertionError("ジョブが時間内に完了しませんでした")


def test_reorder_and_skip_while_paused():
    processed: list[str] = []
    m = JobManager(lambda job: processed.append(job.filename))
    m.pause()  # ワーカーは次ジョブ前で待機 → 全ジョブ queued のまま
    m.enqueue("a.pdf", "a", "out/a")
    m.enqueue("b.pdf", "b", "out/b")
    c = m.enqueue("c.pdf", "c", "out/c")
    a = m.list_jobs()[0]["job_id"]

    assert m.status()["pending"] == 3
    assert m.status()["paused"] is True

    # c を先頭へ、a をスキップ → 実行順は c, b
    assert m.move(c, "up")
    assert m.move(c, "up")
    assert m.skip(a)

    m.resume()
    _wait_idle(m)
    assert processed == ["c.pdf", "b.pdf"]
    statuses = {j["filename"]: j["status"] for j in m.list_jobs()}
    assert statuses["a.pdf"] == "skipped"
    assert statuses["c.pdf"] == "done"


def test_reorder_during_processing():
    started = threading.Event()
    release = threading.Event()
    processed: list[str] = []

    def runner(job):
        if job.filename == "a.pdf":
            started.set()
            release.wait(3)
        processed.append(job.filename)

    m = JobManager(runner)
    m.enqueue("a.pdf", "a", "o")
    m.enqueue("b.pdf", "b", "o")
    c = m.enqueue("c.pdf", "c", "o")

    assert started.wait(3)  # a が処理中
    # 処理中に残りの待機ジョブ(b,c)を入れ替え → c を先に
    assert m.move(c, "up")
    release.set()

    _wait_idle(m)
    assert processed == ["a.pdf", "c.pdf", "b.pdf"]


def test_stop_cancels_pending():
    processed: list[str] = []
    m = JobManager(lambda job: processed.append(job.filename))
    m.pause()
    m.enqueue("a.pdf", "a", "o")
    m.enqueue("b.pdf", "b", "o")
    m.stop()
    _wait_idle(m)
    assert processed == []
    assert all(j["status"] == "canceled" for j in m.list_jobs())


def test_runner_error_marked():
    def boom(job):
        raise RuntimeError("失敗")

    m = JobManager(boom)
    m.enqueue("a.pdf", "a", "o")
    _wait_idle(m)
    job = m.list_jobs()[0]
    assert job["status"] == "error"


def test_runner_error_captures_message_and_traceback():
    def boom(job):
        raise ValueError("NDLが見つかりません")

    m = JobManager(boom)
    m.enqueue("a.pdf", "a", "o")
    _wait_idle(m)
    job = m.list_jobs()[0]
    # サマリは "型名: メッセージ"、詳細は全トレースバック（GUIでクリック表示する）
    assert job["error"] == "ValueError: NDLが見つかりません"
    assert "Traceback" in job["traceback"]
    assert "ValueError" in job["traceback"]
    assert "log_path" in job  # 既定は空文字（server 側でのみ埋まる）


def test_list_jobs_order_finished_then_pending():
    m = JobManager(lambda job: None)
    m.pause()
    m.enqueue("a.pdf", "a", "o")
    m.enqueue("b.pdf", "b", "o")
    # 両方 pending。表示順は登録順（この時点では両方 pending なのでキュー順）
    names = [j["filename"] for j in m.list_jobs()]
    assert names == ["a.pdf", "b.pdf"]
