"""サーバの中のワーカー（スレッドの組）。仕事の台帳から取り、`pipeline.make`（原稿から 1 冊）か
`edit.edit`（直す。P3）を流す。1 冊に走る仕事は 1 つだけ（台帳の `claim_job` が同じデッキの仕事を重ねない）。

決めたこと（理由は `docs/P2-NOTES.md`）:

- **常駐サーバの中のスレッド**にする（v1 の別プロセスの監督と錠は持たない）。サーバは 1 つの置き場に 1 つしか
  動かない（`runtime/server.lock`）ので、ワーカーの錠は要らない。仕事を作った側が `wake()` で直接起こす
- スレッドの数は設定 `worker.parallel`（既定 2）。1 冊の中の並列（ページを同時に描く）はエンジンが持つ。
  **設定は仕事を取る前に読み直す**（`server.refresh_settings`）。数を増やせばスレッドを足し、減らせば番号が
  数を超えたスレッドが手の仕事を終えたあと休む（再起動は要らない）
- 再起動の前（`pause`）は新しい仕事を取らない。「今すぐ」（`stop(abandon=True)`）は子プロセスを止め、台帳に
  何も書かずに抜ける（`pipeline.Abandoned`）。走っていた仕事は次のサーバの起動時の掃除が続きから戻す
- 1 回ぶんの例外はそのスレッドの中で受けて、仕事を失敗にしてから次へ進む（スレッドは死なない）。
  台帳が読めないなどの続く失敗は、待ちを 1, 2, 4 … 秒（上限 30 秒）に広げて回り続ける
- 止めるとき（サーバの `close()`）は合図を立て、走っている子プロセスをグループごと止め、仕事を失敗にしてから
  スレッドを待つ。**台帳を閉じるのはその後**
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Any

from ..paths import DataRoot
from ..settings import Settings
from .edit import edit
from .pipeline import Abandoned, Context, make
from .tools import Tools

log = logging.getLogger("quick_slide.worker")

POLL_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 30.0
UNEXPECTED_MESSAGE = "作っている途中で予期しない問題が起きました。もう一度会話から頼んでください。"


class WorkerPool:
    def __init__(self, server: Any, root: DataRoot, settings: Settings, tools: Tools) -> None:
        self.server = server
        self.root = root
        self.settings = settings
        self.tools = tools
        self.stop_event = threading.Event()
        self.abandon_event = threading.Event()
        self.paused = threading.Event()
        self._cond = threading.Condition()
        self._woken = 0
        self._threads: list[threading.Thread] = []
        self._busy: dict[str, str | None] = {}
        self._spawn_lock = threading.Lock()
        self.target = settings.worker.parallel
        self.errors = 0

    # --- 起動・停止 ---
    def start(self) -> None:
        self.adjust(self.settings.worker.parallel)
        log.info("ワーカーを起こしました（%d 本）", len(self._threads))

    def adjust(self, parallel: int) -> None:
        """同時に進める仕事の数を変える（設定を読み直したとき）。足りなければスレッドを足す。減らすときは止めずに休ませる。"""
        with self._spawn_lock:
            self.target = parallel
            while len(self._threads) < parallel and not self.stop_event.is_set():
                index = len(self._threads)
                name = f"quick-slide-worker-{index + 1}"
                t = threading.Thread(target=self._loop, name=name, args=(name, index), daemon=True)
                self._busy[name] = None
                self._threads.append(t)
                t.start()
        self.wake()

    def wake(self) -> None:
        with self._cond:
            self._woken += 1
            self._cond.notify_all()

    def pause(self) -> None:
        """新しい仕事を取らない（再起動の前）。走っている仕事はそのまま続ける。"""
        self.paused.set()
        self.wake()

    def busy_jobs(self) -> list[str]:
        return [v for v in self._busy.values() if v]

    def stop(self, timeout: float = 20.0, *, abandon: bool = False) -> None:
        if abandon:
            self.abandon_event.set()
        self.stop_event.set()
        self.wake()
        deadline = time.monotonic() + timeout
        for t in list(self._threads):
            t.join(timeout=max(0.1, deadline - time.monotonic()))
        alive = [t.name for t in self._threads if t.is_alive()]
        if alive:
            log.error("止まらなかったワーカー: %s", ", ".join(alive))

    def status(self) -> dict[str, Any]:
        return {"threads": len(self._threads), "alive": sum(1 for t in self._threads if t.is_alive()),
                "parallel": self.target, "busy": sum(1 for v in self._busy.values() if v), "errors": self.errors,
                "paused": self.paused.is_set()}

    # --- 1 本の回り ---
    def _context(self) -> Context:
        return Context(store=self.server.store, lock=self.server.store_lock, root=self.root,
                       settings=self.server.settings, tools=self.tools, stop=self.stop_event,
                       abandon=self.abandon_event)

    def _wait(self, seconds: float) -> None:
        with self._cond:
            seen = self._woken
            self._cond.wait_for(lambda: self._woken != seen or self.stop_event.is_set(), timeout=seconds)

    def _refresh(self) -> None:
        refresh = getattr(self.server, "refresh_settings", None)
        if refresh is None:
            return
        try:
            refresh()
        except Exception:  # noqa: BLE001 - 読み直せなくても、前の設定で回り続ける
            log.error("設定を読み直せませんでした（前の設定で続けます）\n%s", traceback.format_exc())

    def _loop(self, name: str, index: int) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            self._refresh()
            if index >= self.target or self.paused.is_set():
                self._wait(POLL_SECONDS)
                continue
            try:
                with self.server.store_lock:
                    store = self.server.store
                    job = store.claim_job(name) if store is not None and not self.paused.is_set() else None
                backoff = 1.0
            except Exception:  # noqa: BLE001 - 台帳が読めない。待ちを広げて回り続ける
                self.errors += 1
                log.error("仕事を取れませんでした（続行）\n%s", traceback.format_exc())
                self._wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            if job is None:
                self._wait(POLL_SECONDS)
                continue
            self._busy[name] = job["id"]
            try:
                self._run_one(job)
            except Abandoned:
                log.info("仕事 %s を手放しました（今すぐ再起動）", job["id"])
            finally:
                self._busy[name] = None
        self._busy[name] = None

    def _run_one(self, job: dict[str, Any]) -> None:
        ctx = self._context()
        state = "failed"
        error_class = None
        try:
            state = edit(ctx, job) if job.get("kind") == "edit" else make(ctx, job)
        except Exception as exc:  # noqa: BLE001 - 1 つの仕事の失敗でスレッドを止めない
            self.errors += 1
            error_class = exc.__class__.__name__
            log.error("仕事 %s が例外で止まりました\n%s", job["id"], traceback.format_exc())
            try:
                ctx.call(ctx.store.fail_run, job["run_id"], message=UNEXPECTED_MESSAGE, failure_kind="internal")
            except Exception:  # noqa: BLE001
                log.error("失敗を書けませんでした\n%s", traceback.format_exc())
        ctx.call(lambda: None)   # 手放したなら、ここで Abandoned（終わりを書かない）
        try:
            ctx.call(ctx.store.finish_job, job["id"], "done" if state == "done" else "failed",
                     error_class=error_class)
        except Exception:  # noqa: BLE001
            log.error("仕事の終わりを書けませんでした\n%s", traceback.format_exc())
