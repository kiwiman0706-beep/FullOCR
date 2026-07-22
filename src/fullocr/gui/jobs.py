"""GUI バッチのジョブ管理（一時停止／中止／スキップ／並べ替え対応）。

変換は「1冊 = 1ジョブ」を1本のワーカースレッドで直列処理する（各変換自体が
CPU を使い切るため）。処理の粒度は"ファイル単位"で、実行中の1冊は中断できないが、
その前後で以下を制御できる:

- pause / resume : 次の1冊に進む前で待機／再開
- stop          : 実行中の1冊が終わったら残りをキャンセル
- skip(id)      : まだ待機中のジョブをスキップ（実行中は不可）
- move(id, dir) : 待機中ジョブの順序を上下入れ替え（処理中でも可）

`runner(job)` は1冊を実際に処理する関数（実処理は server 側が渡す。テストでは
フェイクを渡す）。runner が job.status を "skipped" にしたらそれを尊重し、正常終了
すれば "done"、例外なら "error" にする。
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    id: str
    filename: str
    path: str
    output_dir: str
    status: str = "queued"  # queued / processing / done / skipped / error / canceled
    error: str = ""          # 例外の1行メッセージ（表示のサマリ）
    traceback: str = ""      # 例外の全トレースバック（クリックで開く詳細）
    percent: int = 0         # 処理中の進捗（0-100、段階ベース）
    step: str = ""           # 処理中の段階ラベル（「OCR実行中…」など）
    # 1冊分の設定・フラグ（server が詰める。テストでは任意）。
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "job_id": self.id,
            "filename": self.filename,
            "status": self.status,
            "output_dir": self.output_dir,
            "error": self.error,
            "traceback": self.traceback,
            "log_path": self.meta.get("log_path", ""),
            "percent": self.percent,
            "step": self.step,
            "pending": self.status == "queued",
        }


_TERMINAL = {"done", "skipped", "error", "canceled"}


class JobManager:
    def __init__(self, runner: Callable[[Job], None]) -> None:
        self._runner = runner
        self._cv = threading.Condition(threading.RLock())
        self._jobs: dict[str, Job] = {}
        self._insertion: list[str] = []  # 全ジョブの登録順（表示用）
        self._queue: list[str] = []      # 待機中ジョブの実行順
        self._current: str | None = None
        self._paused = False
        self._stop = False
        self._worker: threading.Thread | None = None
        # 全ジョブ横断の進捗ログ（コンソール表示用）。エンジンの生ログも入るので
        # やや大きめに保持し、古い行から捨てる。
        self._log: deque[dict] = deque(maxlen=1200)

    # ---- 投入 -----------------------------------------------------------
    def enqueue(self, filename: str, path: str, output_dir: str, meta: dict | None = None) -> str:
        job = Job(id=uuid.uuid4().hex, filename=filename, path=path,
                  output_dir=output_dir, meta=meta or {})
        with self._cv:
            self._jobs[job.id] = job
            self._insertion.append(job.id)
            self._queue.append(job.id)
            self._ensure_worker()
            self._cv.notify_all()
        return job.id

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._loop, daemon=True)
            self._worker.start()

    # ---- 制御 -----------------------------------------------------------
    def pause(self) -> None:
        with self._cv:
            self._paused = True
            self._cv.notify_all()

    def resume(self) -> None:
        with self._cv:
            self._paused = False
            self._ensure_worker()
            self._cv.notify_all()

    def stop(self) -> None:
        """実行中の1冊が終わり次第、残りをキャンセル。"""
        with self._cv:
            self._stop = True
            self._paused = False
            self._cv.notify_all()

    def skip(self, job_id: str) -> bool:
        with self._cv:
            if job_id in self._queue:
                self._queue.remove(job_id)
                self._jobs[job_id].status = "skipped"
                return True
            return False

    def move(self, job_id: str, direction: str) -> bool:
        """待機キュー内で上(up)/下(down)へ1つ動かす。"""
        with self._cv:
            if job_id not in self._queue:
                return False
            i = self._queue.index(job_id)
            j = i - 1 if direction == "up" else i + 1
            if j < 0 or j >= len(self._queue):
                return False
            self._queue[i], self._queue[j] = self._queue[j], self._queue[i]
            return True

    # ---- 進捗 -----------------------------------------------------------
    def progress(self, job_id: str, percent: float, label: str) -> None:
        """処理中ジョブの進捗を更新し、横断ログにも1行足す（runner から呼ぶ）。"""
        with self._cv:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.percent = max(0, min(100, int(percent)))
            job.step = label
            self._append_log(job.filename, label, job.percent, kind="stage")

    def log_line(self, job_id: str, message: str) -> None:
        """エンジン(NDL/Docling)の生の出力を1行、横断ログに積む（runner から呼ぶ）。

        呼び出し頻度が高い（ページ毎など）ので percent は触らず、kind=engine で分類
        だけしておく（GUI 側で表示/非表示を切り替えられる）。
        """
        with self._cv:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._append_log(job.filename, message, None, kind="engine")

    def _append_log(
        self, filename: str, message: str, percent: int | None = None, *, kind: str = "event"
    ) -> None:
        """ロック保持中に呼ぶ前提。コンソール表示用の1行を積む。

        kind: "stage"（段階の節目）/ "engine"（エンジンの生ログ）/ "event"（開始・完了等）。
        """
        self._log.append({
            "t": time.strftime("%H:%M:%S"),
            "file": filename,
            "msg": message,
            "percent": percent,
            "kind": kind,
        })

    def log_tail(self, n: int = 200) -> list[dict]:
        with self._cv:
            return list(self._log)[-n:]

    # ---- 状態 -----------------------------------------------------------
    def status(self) -> dict:
        with self._cv:
            running = self._current is not None or bool(self._queue)
            return {
                "running": running,
                "paused": self._paused,
                "current": self._current,
                "pending": len(self._queue),
                "log": list(self._log)[-300:],
            }

    def list_jobs(self) -> list[dict]:
        """表示順: 非待機（登録順）→ 待機（キュー順）。"""
        with self._cv:
            pending = set(self._queue)
            non_pending = [jid for jid in self._insertion if jid not in pending]
            ordered = non_pending + list(self._queue)
            return [self._jobs[jid].to_dict() for jid in ordered]

    # ---- ワーカー -------------------------------------------------------
    def _loop(self) -> None:
        while True:
            with self._cv:
                while self._paused and not self._stop:
                    self._cv.wait()
                if self._stop:
                    for jid in self._queue:
                        self._jobs[jid].status = "canceled"
                    self._queue.clear()
                    self._stop = False
                    self._worker = None
                    return
                if not self._queue:
                    self._worker = None
                    return
                job = self._jobs[self._queue.pop(0)]
                job.status = "processing"
                job.percent = 0
                job.step = "開始…"
                self._current = job.id
                self._append_log(job.filename, "変換を開始しました", 0)
            # 実処理はロック外（長時間）
            try:
                self._runner(job)
                if job.status == "processing":
                    job.status = "done"
                with self._cv:
                    if job.status == "done":
                        job.percent = 100
                        job.step = "完了"
                        self._append_log(job.filename, "完了しました", 100)
                    elif job.status == "skipped":
                        job.step = "スキップ（変換済み）"
                        self._append_log(job.filename, "変換済みのためスキップしました")
            except Exception as e:  # noqa: BLE001 - 1冊失敗で全体を止めない
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                job.traceback = traceback.format_exc()
                job.step = "エラー"
                with self._cv:
                    self._append_log(job.filename, f"エラー: {job.error}")
            finally:
                with self._cv:
                    self._current = None
