"""起動 / 停止 / 状態確認の入口（`python -m quick_slide.server ...`）。

会話の入口のスキル（P2）はここを呼ぶ。stdout の最終行は JSON。
終了コード: 0=成功 / 2=入力・設定不正 / 3=起動できない・稼働していない / 4=処理失敗。
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..errors import QuickSlideError, ValidationError
from ..paths import DataRoot
from ..settings import BROWSER_MODES, Settings
from .app import SESSION_HEADER, QuickSlideServer
from .runtime import http_host, pid_alive, read_record, status

log = logging.getLogger("quick_slide.server.cli")


def _print_json(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def _configure_logging(root: DataRoot, *, to_stderr: bool) -> None:
    root.runtime_dir.mkdir(parents=True, exist_ok=True)
    path = root.server_log_path
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)
    handlers: list[logging.Handler] = [logging.FileHandler(path, encoding="utf-8")]
    if to_stderr:
        handlers.append(logging.StreamHandler(sys.stderr))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    logger = logging.getLogger("quick_slide")
    logger.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        logger.addHandler(h)


def _root(args: argparse.Namespace) -> DataRoot:
    return DataRoot.resolve(args.home)


def cmd_serve(args: argparse.Namespace) -> int:
    root = _root(args)
    _configure_logging(root, to_stderr=not args.quiet)
    try:
        settings = Settings.load(root.settings_path)
        server = QuickSlideServer(root, settings=settings, host=args.host, port=args.port, static_root=args.static_root)
        server.start()
    except QuickSlideError as exc:
        _print_json({"ok": False, "code": exc.__class__.__name__, "message": str(exc)})
        return exc.exit_code
    _print_json({"ok": True, "serving": True, "url": server.url, "pid": os.getpid(), "instance_id": server.instance_id})
    try:
        server.run_after_start()
    finally:
        pass
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    root = _root(args)
    current = status(root, timeout=args.timeout)
    if current.get("running"):
        _print_json({"ok": True, "already_running": True,
                     **{k: current[k] for k in ("pid", "url", "instance_id", "started_at")}})
        return 0
    root.ensure()
    out_fd = os.open(root.server_stdio_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    cmd = [sys.executable, "-m", "quick_slide.server", "--home", str(root.root), "serve", "--quiet"]
    if args.host is not None:
        cmd += ["--host", args.host]
    if args.port is not None:
        cmd += ["--port", str(args.port)]
    if args.static_root is not None:
        cmd += ["--static-root", str(args.static_root)]
    try:
        child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out_fd, stderr=out_fd, start_new_session=True, close_fds=True)
    finally:
        os.close(out_fd)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        st = status(root, timeout=1.0)
        if st.get("running"):
            _print_json({"ok": True, "already_running": False,
                         **{k: st[k] for k in ("pid", "url", "instance_id", "started_at")}})
            return 0
        if child.poll() is not None:
            break
        time.sleep(0.1)
    rc = child.poll()
    _print_json({"ok": False, "code": "start_failed", "message": "サーバの起動を確認できませんでした。runtime/server.out と runtime/server.log を確認してください。",
                 "child_exit_code": rc, "stdio": str(root.server_stdio_path)})
    return 3


def cmd_stop(args: argparse.Namespace) -> int:
    root = _root(args)
    try:
        record = read_record(root)
    except ValueError as exc:
        _print_json({"ok": False, "stopped": False, "reason": "server_json_corrupt", "detail": str(exc)})
        return 4
    if record is None:
        _print_json({"ok": True, "stopped": False, "reason": "not_running"})
        return 0
    if not pid_alive(record.pid):
        _print_json({"ok": True, "stopped": False, "reason": "stale_pid", "pid": record.pid})
        return 0
    conn = http.client.HTTPConnection(record.host, record.port, timeout=args.timeout)
    try:
        conn.request("POST", "/api/server/stop", body=b"{}", headers={
            "Host": f"{http_host(record.host)}:{record.port}", "Content-Type": "application/json",
            "Content-Length": "2", SESSION_HEADER: record.session_token,
        })
        resp = conn.getresponse()
        body = resp.read()
    except (OSError, http.client.HTTPException) as exc:
        _print_json({"ok": False, "stopped": False, "reason": "unreachable", "detail": exc.__class__.__name__, "pid": record.pid})
        return 4
    finally:
        conn.close()
    if resp.status != 202:
        _print_json({"ok": False, "stopped": False, "reason": "rejected", "http_status": resp.status, "body": body.decode("utf-8", "replace")})
        return 4
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if not pid_alive(record.pid):
            _print_json({"ok": True, "stopped": True, "pid": record.pid, "instance_id": record.instance_id})
            return 0
        time.sleep(0.1)
    _print_json({"ok": False, "stopped": False, "reason": "timeout", "pid": record.pid})
    return 4


def cmd_status(args: argparse.Namespace) -> int:
    """`status()` の辞書をそのまま出す。サーバが動いていれば 0、動いていなければ 3。"""
    st = status(_root(args), timeout=args.timeout)
    _print_json(st)
    return 0 if st.get("running") else 3


# --- launch ensure: ブラウザを開く3段の経路 ---
# **経路1を実行するのは呼ぶ側**（スキルが URL をランタイムに見せる）。ここがやるのはその結果を待つことだけで、
# 統合ブラウザで開けたかどうかを CLI から見る手段は無い。**開けた証拠は、サーバに attach が届くことだけ**。
# だからここは「届いたか」しか見ない。届かない理由（開かなかった / 開いたが遅い / 人が閉じた）は区別しない。
LAUNCH_ATTACHED_ROUTE_INTEGRATED = "integrated"   # こちらが何も開かないうちに attach が来た
LAUNCH_ATTACHED_ROUTE_EXTERNAL = "external"       # 外部ブラウザを開いたら attach が来た
LAUNCH_ROUTE_URL_ONLY = "url_only"                # どちらでも来なかった（URL を会話に残すのが答え）
LAUNCH_ROUTE_MANUAL = "manual"                    # 設定で「開かずURLだけ」

# 開けなかったときに会話へ出す1文（経路3）。**URL は経路1・2 が成功しても常に会話に残す**ので、
# ここは「開けなかったときだけ足す文」である
LAUNCH_NOT_ATTACHED_MESSAGE = "ブラウザで開けたか確認できませんでした。この URL をブラウザで開いてください。"
LAUNCH_MANUAL_MESSAGE = "この URL をブラウザで開いてください。"
LAUNCH_NOT_FOUND_MESSAGE = "そのリンクは見つかりません。会話に残された URL をそのまま開いてください。"
LAUNCH_SERVER_DOWN_MESSAGE = "Quick Slide のサーバが動いていません。先に起動してください。"
# 開く先を、いま動いているサーバ自身に限る。**この口はブラウザを開くので、任意の URL を渡せてはいけない。**
LAUNCH_URL_FOREIGN_MESSAGE = "この URL は Quick Slide の画面ではありません。start が返した URL をそのまま渡してください。"
# URL には起こしの id が入っている（`?launch=<id>&token=<...>`）。**別々に渡された 2 つが食い違ったら断る。**
# 食い違ったまま進むと、開く画面と待つ行が別々になり、「開いたのに attach が来ない」に化ける
LAUNCH_URL_MISMATCH_MESSAGE = "URL と起こしの id が食い違っています。start が返した組をそのまま渡してください。"


def _launch_row(host: str, port: int, launch_id: str, *, timeout: float = 5.0) -> tuple[int, Any]:
    """`GET /api/launches/<id>`。届かなければ (0, None)。"""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", f"/api/launches/{launch_id}", headers={"Host": f"{http_host(host)}:{port}"})
        resp = conn.getresponse()
        body = resp.read()
    except (OSError, http.client.HTTPException):
        return 0, None
    finally:
        conn.close()
    try:
        return resp.status, json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return resp.status, None


def _wait_for_attach(host: str, port: int, launch_id: str, seconds: float) -> dict[str, Any] | None:
    """attach が届くまで待つ。届かなければ None。**待ち終わりを 1 回は必ず確かめてから諦める**。"""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        code, row = _launch_row(host, port, launch_id)
        if code == 200 and isinstance(row, dict) and row.get("state") == "attached":
            return row
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.25)


def _open_externally(url: str) -> bool:
    """外部ブラウザで開く。返すのは**開く指示を出せたか**であって、開いたかでも attach したかでもない。"""
    try:
        return bool(webbrowser.open(url))
    except (OSError, webbrowser.Error):
        return False


def cmd_launch(args: argparse.Namespace) -> int:
    root = _root(args)
    st = status(root, timeout=args.timeout)
    if not st.get("running"):
        _print_json({"ok": False, "code": "server_not_running", "message": LAUNCH_SERVER_DOWN_MESSAGE,
                     "reason": st.get("reason")})
        return 3
    host, port, base = st["host"], st["port"], st["url"]
    if not args.url.startswith(base + "/"):
        _print_json({"ok": False, "code": "url_not_ours", "message": LAUNCH_URL_FOREIGN_MESSAGE, "url": args.url})
        return 2
    in_url = parse_qs(urlsplit(args.url).query).get("launch", [None])[0]
    if in_url != args.launch_id:
        _print_json({"ok": False, "code": "launch_url_mismatch", "message": LAUNCH_URL_MISMATCH_MESSAGE,
                     "launch_id": args.launch_id})
        return 2
    settings = Settings.load(root.settings_path)
    mode = args.mode or settings.browser.mode
    wait = settings.browser.attach_wait_seconds if args.wait is None else args.wait
    code, row = _launch_row(host, port, args.launch_id, timeout=args.timeout)
    if code == 404:
        _print_json({"ok": False, "code": "launch_not_found", "message": LAUNCH_NOT_FOUND_MESSAGE,
                     "launch_id": args.launch_id})
        return 4
    if code != 200 or not isinstance(row, dict):
        _print_json({"ok": False, "code": "server_unreachable", "message": LAUNCH_SERVER_DOWN_MESSAGE})
        return 3

    opened = False
    attached = row if row.get("state") == "attached" else None
    if attached is None and mode == "auto":
        attached = _wait_for_attach(host, port, args.launch_id, wait)   # 経路1（統合ブラウザ）の結果を待つ
    if attached is not None:
        route = LAUNCH_ATTACHED_ROUTE_INTEGRATED
    elif mode == "manual":
        route = LAUNCH_ROUTE_MANUAL
    else:
        opened = _open_externally(args.url)                             # 経路2（外部ブラウザ）
        attached = _wait_for_attach(host, port, args.launch_id, wait) if opened else None
        route = LAUNCH_ATTACHED_ROUTE_EXTERNAL if attached else LAUNCH_ROUTE_URL_ONLY
    message = None
    if attached is None:
        message = LAUNCH_MANUAL_MESSAGE if route == LAUNCH_ROUTE_MANUAL else LAUNCH_NOT_ATTACHED_MESSAGE
    _print_json({"ok": True, "launch_id": args.launch_id, "url": args.url, "mode": mode,
                 "waited_seconds": wait, "route": route, "opened": opened,
                 "attached": attached is not None,
                 # 統合ブラウザの確実な識別手段は無いので、これは**推定値**（診断用。分岐に使わない）
                 "attached_via": attached.get("attached_via") if attached else None,
                 "message": message})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quick_slide.server", description="Quick Slide 常駐サーバ")
    p.add_argument("--home", default=None, help="データ置き場（既定: $QUICK_SLIDE_HOME または ~/.quick-slide）")
    p.add_argument("--timeout", type=float, default=10.0, help="起動・停止・状態確認の待ち秒数")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("serve", "start"):
        sp = sub.add_parser(name)
        sp.add_argument("--host", default=None,
                        help="bind 先（127.0.0.1 / localhost / ::1 か、この機械のアドレス。設定 server.host を上書き）")
        sp.add_argument("--port", type=int, default=None, help="ポート（設定 server.port を上書き。0 は自動割当）")
        sp.add_argument("--static-root", default=None, type=Path, help=argparse.SUPPRESS)
        sp.add_argument("--quiet", action="store_true", help="stderr にログを出さない")
    sub.add_parser("stop")
    sub.add_parser("status")
    launch = sub.add_parser("launch", help="ブラウザの起こし（3段経路）")
    lsub = launch.add_subparsers(dest="launch_command", required=True)
    ensure = lsub.add_parser("ensure", help="attach を待ち、来なければ外部ブラウザで開く")
    ensure.add_argument("launch_id")
    ensure.add_argument("--url", required=True, help="start が返した URL（launch トークン付き）")
    ensure.add_argument("--mode", choices=BROWSER_MODES, default=None,
                        help="設定 browser.mode を 1 回だけ上書きする（既定は設定と QUICK_SLIDE_BROWSER）")
    ensure.add_argument("--wait", type=float, default=None,
                        help="各経路で attach を待つ秒数（既定は設定 browser.attach_wait_seconds）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "serve":
            return cmd_serve(args)
        if args.command == "start":
            return cmd_start(args)
        if args.command == "stop":
            return cmd_stop(args)
        if args.command == "launch":
            return cmd_launch(args)
        return cmd_status(args)
    except ValidationError as exc:
        _print_json({"ok": False, "code": "ValidationError", "message": str(exc)})
        return 2
