"""画面の「再起動」ボタン（P4）。サーバが自分で起動し直し、新しい設定で起動できなければ前の設定に戻して起動する。

流れ（`POST /api/server/restart` `{mode: "wait"|"now"}`）:

1. 断る: このプロセスが常駐の `serve` でない（試験のスレッドの中など）・もう再起動している・settings.json が読めない・
   待ち受けが「ぜんぶの口」や名前（`bind_refusal`）。どれも何も変えずに 4xx で返す
2. いま動いている待ち受けを **last-good** として控える（`runtime/last-good-listen.json`。起動できたときにも書いている）
3. 新しい仕事を受けない（取り込み・直しの口は 503 `restarting`。ワーカーは待っている仕事を取らない）
4. 答える（202）: 走っている仕事の数・新しい設定から求めた URL（名前があれば 1 つ目、無ければアドレス＋ポート）
5. 別のスレッドで: `wait` なら走っている仕事が終わるのを待つ（上限 `WAIT_LIMIT_SECONDS`。超えたら今すぐと同じ）。
   後継（`python -m quick_slide.server --home … serve --after-pid <自分> --restart-id …`）を、新しいセッション
   （プロセスのグループ）で起こしてから、自分は止まる。「今すぐ」は子プロセスを止め、台帳に何も書かずに抜ける
   （次のサーバの起動時の掃除が、描く・取り込む段は続きから、書く段は理由つきで失敗にする。P2・P3 と同じ）
6. 後継（`serve_after_restart`）: 前のプロセスが終わるのを待ち、新しい設定で起動する。起動できなければ（bind できない・
   設定が通らない）**settings.json の `server` の段だけを last-good に戻し**、前の設定で起動して、理由を
   `runtime/restart.json` に書く。画面は health の `restart` でそれを見せる。どちらでも起動できなければ、その理由を
   書いて終わる（前の設定で起動できなかったのは、ほかのアプリがポートを取ったときなど）

**利用者がサーバの無い状態に置かれないこと**が一番の約束。後継を起こしてから止まり、後継は前の設定へ戻ってでも起動する。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .. import settings as S
from ..clock import utcnow
from ..errors import QuickSlideError, ValidationError
from ..oscompat import group_kwargs
from ..paths import DataRoot
from .errors import ApiError

log = logging.getLogger("quick_slide.server.restart")

MODES = ("wait", "now")
WAIT_LIMIT_SECONDS = 30 * 60          # 「終わるのを待って」の上限。超えたら今すぐと同じにする
OLD_EXIT_WAIT_SECONDS = 90.0          # 後継が前のプロセスの終わりを待つ上限
PORT_FREE_WAIT_SECONDS = 15.0
FALLBACK_MESSAGE = "新しい設定で起動できなかったので、前の設定に戻しました（{reason}）"
SRC_DIR = Path(__file__).resolve().parents[2]   # `quick_slide` の親（後継の PYTHONPATH）


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_last_good(root: DataRoot, listen: dict[str, Any]) -> None:
    _write_json(root.last_good_listen_path, {"server": listen, "saved_at": utcnow()})


def read_last_good(root: DataRoot) -> dict[str, Any] | None:
    data = _read_json(root.last_good_listen_path)
    server = data.get("server") if data else None
    return server if isinstance(server, dict) and isinstance(server.get("host"), str) else None


def write_result(root: DataRoot, result: dict[str, Any]) -> None:
    _write_json(root.restart_result_path, {**result, "at": utcnow()})


def read_result(root: DataRoot) -> dict[str, Any] | None:
    return _read_json(root.restart_result_path)


# --- 前のサーバ（ボタンを受けた側） --------------------------------------------------------------------------------

def begin(app: Any, mode: Any) -> dict[str, Any]:
    """再起動を受け付ける（答えの本文を返す）。実際の待ち・後継の起動・停止は別のスレッドでする。"""
    if mode not in MODES:
        raise ApiError(400, "bad_restart_mode", "再起動のしかたは「終わるのを待つ」か「今すぐ」から選んでください。")
    if not app.restartable:
        raise ApiError(409, "restart_unavailable",
                       "この画面からは再起動できません（コマンドで起動し直してください）。")
    if app.restarting is not None:
        raise ApiError(409, "restart_in_progress", "もう再起動しています。少し待ってから画面を読み直してください。")
    try:
        disk = S.Settings.load(app.root.settings_path)
    except ValidationError as exc:
        raise ApiError(400, "settings_unreadable", f"設定ファイルを読み込めないため、再起動しません（{exc}）。"
                       "ファイルを直してから、もう一度押してください。") from None
    refusal = S.bind_refusal(disk.server.host)
    if refusal is not None:
        raise ApiError(400, "bad_bind_address", refusal)
    new_listen = S.listen_of(disk.server)
    save_last_good(app.root, app.running_listen())
    restart_id = secrets.token_hex(8)
    running = app.workers.busy_jobs() if app.workers is not None else []
    app.restarting = {"id": restart_id, "mode": mode, "started": time.time(), "running_jobs": len(running)}
    if app.workers is not None:
        app.workers.pause()
    url = S.expected_url(new_listen)
    same = url is None or url == app.url
    log.info("再起動を受け付けました id=%s mode=%s 走っている仕事=%d 新しい URL=%s", restart_id, mode, len(running), url)
    threading.Thread(target=_run, args=(app, restart_id, mode), name="quick-slide-restart", daemon=True).start()
    return {"restarting": True, "restart_id": restart_id, "mode": mode, "running_jobs": len(running),
            "expected_url": url or app.url, "same_url": same, "instance_id": app.instance_id}


def _run(app: Any, restart_id: str, mode: str) -> None:
    try:
        if mode == "wait" and app.workers is not None:
            deadline = time.monotonic() + WAIT_LIMIT_SECONDS
            while app.workers.busy_jobs() and time.monotonic() < deadline:
                time.sleep(0.2)
            if app.workers.busy_jobs():
                log.warning("待ちの上限を越えたので、走っている仕事を手放して再起動します")
        spawn_successor(app, restart_id)
    except Exception:  # noqa: BLE001 - 後継を起こせなければ止まらない（サーバの無い状態にしない）
        log.exception("後継を起こせなかったので、再起動をやめます")
        app.restarting = None
        if app.workers is not None:
            app.workers.paused.clear()
            app.workers.wake()
        return
    app.request_stop(abandon=True)


def successor_command(app: Any, restart_id: str) -> list[str]:
    cmd = [sys.executable, "-m", "quick_slide.server", "--home", str(app.root.root), "serve", "--quiet",
           "--after-pid", str(os.getpid()), "--restart-id", restart_id]
    if app.static_root_arg is not None:
        cmd += ["--static-root", str(app.static_root_arg)]
    return cmd


def spawn_successor(app: Any, restart_id: str) -> subprocess.Popen:
    env = dict(os.environ)
    paths = [str(SRC_DIR)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p and p != str(SRC_DIR)]
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    app.root.runtime_dir.mkdir(parents=True, exist_ok=True)
    out_fd = os.open(app.root.server_stdio_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
    try:
        child = subprocess.Popen(successor_command(app, restart_id), stdin=subprocess.DEVNULL, stdout=out_fd,
                                 stderr=out_fd, close_fds=True, env=env, cwd=os.getcwd(), **group_kwargs())
    finally:
        os.close(out_fd)
    log.info("後継を起こしました pid=%d", child.pid)
    return child


# --- 後継（新しいプロセス） ------------------------------------------------------------------------------------

def wait_for_exit(pid: int, seconds: float = OLD_EXIT_WAIT_SECONDS) -> bool:
    from .runtime import pid_alive

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def start_after_restart(root: DataRoot, restart_id: str, make_server) -> Any:
    """新しい設定で起動する。だめなら `server` の段を last-good に戻して起動する。起動したサーバを返す。

    `make_server(settings)` は起動済み（`start()` 済み）のサーバを返すか、`QuickSlideError` を投げる。
    """
    reason = None
    try:
        settings = S.Settings.load(root.settings_path)
        server = make_server(settings)
        write_result(root, {"restart_id": restart_id, "ok": True, "fallback": False, "url": server.url,
                            "message": None})
        return server
    except (QuickSlideError, OSError) as exc:
        reason = str(exc) or exc.__class__.__name__
        log.error("新しい設定で起動できませんでした: %s", reason)
    last = read_last_good(root)
    if last is None:
        write_result(root, {"restart_id": restart_id, "ok": False, "fallback": False, "url": None,
                            "message": f"新しい設定で起動できず、戻す前の設定の控えもありませんでした（{reason}）"})
        raise SystemExit(3)
    S.restore_server_section(root.settings_path, last)
    message = FALLBACK_MESSAGE.format(reason=_short(reason))
    try:
        settings = S.Settings.load(root.settings_path)
        server = make_server(settings)
    except (QuickSlideError, OSError) as exc:
        write_result(root, {"restart_id": restart_id, "ok": False, "fallback": True, "url": None,
                            "message": f"前の設定でも起動できませんでした（{_short(str(exc))}）。"
                                       "ほかのアプリがポートを使っていないか確かめて、コマンドで起動してください"})
        raise SystemExit(3) from exc
    write_result(root, {"restart_id": restart_id, "ok": True, "fallback": True, "url": server.url, "message": message,
                        "reason": reason})
    return server


def _short(text: str | None, limit: int = 200) -> str:
    text = (text or "理由は分かりません").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def public_result(root: DataRoot) -> dict[str, Any] | None:
    """health に出す最後の再起動の結果（画面がつなぎ直したあとで読む）。"""
    r = read_result(root)
    if not r:
        return None
    return {k: r.get(k) for k in ("restart_id", "ok", "fallback", "url", "message", "at")}
