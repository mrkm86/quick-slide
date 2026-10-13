"""子プロセスを 1 つ動かす（台本係の CLI・エンジン共通）。

- 標準入力へは別のスレッドで書く（長い原稿でも、子が読み始める前に詰まらない）
- 標準エラーは 1 行ずつ読んで `on_line` に渡す（エンジンの進みを拾う）。ファイルにも写す（0600）
- 時間切れ・止める合図（`stop`）で、**プロセスのグループごと**止める（エンジンが起こした codex や Chrome も残さない）
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..oscompat import end_group, kill_group, popen_group


@dataclass
class ProcResult:
    rc: int | None
    stdout: str
    stderr_tail: list[str] = field(default_factory=list)
    timed_out: bool = False
    stopped: bool = False
    launch_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out and not self.stopped and self.launch_error is None


def _private_writer(path: Path | None):
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    return os.fdopen(fd, "w", encoding="utf-8")


def run_process(cmd: list[str], *, cwd: Path, env: Mapping[str, str], timeout: float,
                stop: threading.Event | None = None, stdin_text: str | None = None,
                on_line: Callable[[str], None] | None = None, stdout_path: Path | None = None,
                stderr_path: Path | None = None, tail_lines: int = 200) -> ProcResult:
    try:
        proc = popen_group(cmd, cwd=str(cwd), env=dict(env), stdin=subprocess.PIPE if stdin_text is not None
                           else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        return ProcResult(None, "", launch_error=exc.__class__.__name__)
    out_chunks: list[str] = []
    tail: deque[str] = deque(maxlen=tail_lines)
    out_file = _private_writer(stdout_path)
    err_file = _private_writer(stderr_path)

    def feed() -> None:
        try:
            assert proc.stdin is not None
            # バイトで書く。テキストの包みに書くと、Windows では \n が \r\n に変わって台本係に届く（N16）
            proc.stdin.buffer.write((stdin_text or "").encode("utf-8"))
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()  # type: ignore[union-attr]
            except (BrokenPipeError, OSError, ValueError):
                pass

    def read_out() -> None:
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(65536), ""):
            out_chunks.append(chunk)
            if out_file is not None:
                out_file.write(chunk)

    def read_err() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip("\n")
            tail.append(line)
            if err_file is not None:
                err_file.write(line + "\n")
                err_file.flush()
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:  # noqa: BLE001 - 進みの読み取りの失敗で、子を止めない
                    pass

    threads = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
    if stdin_text is not None:
        threads.append(threading.Thread(target=feed, daemon=True))
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    timed_out = stopped = False
    while proc.poll() is None:
        if stop is not None and stop.is_set():
            stopped = True
            kill_group(proc)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            kill_group(proc)
            break
        time.sleep(0.1)
    for t in threads:
        t.join(timeout=2)
    if any(t.is_alive() for t in threads):
        # 子は終わったのに、孫（子が起こしたもの）が出力の口を握ったまま残っている。グループごと片付ける
        kill_group(proc)
        for t in threads:
            t.join(timeout=10)
    end_group(proc)
    for f in (out_file, err_file, proc.stdout, proc.stderr):
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
    return ProcResult(proc.returncode, "".join(out_chunks), list(tail), timed_out=timed_out, stopped=stopped)
