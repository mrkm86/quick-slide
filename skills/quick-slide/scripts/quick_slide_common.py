"""会話から呼ぶ CLI（`quick_slide_start.py`・`quick_slide_template.py`）が共有する部品。

サーバの探し方・起こし方（古い版の入れ替え・Codex の砂場の扱いを含む）・`server.json` の読み・HTTP・会話の識別子の採り方・
失敗の形（`Failure`）と出力（`emit`）を 1 か所に置く。呼び出し側の CLI は、ここを import して使う（再実装しない）。

秘匿: セッショントークン・会話の識別子の生値は、stdout・stderr・ファイルに書かない（呼び出し側も守る）。
標準ライブラリだけを使う（サーバ本体を import しない）。
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_STARTUP = 3
EXIT_FAILED = 4

SESSION_HEADER = "X-Quick-Slide-Session"
ENV_REPO = "QUICK_SLIDE_REPO"
ENV_HOME = "QUICK_SLIDE_HOME"
# 「実体」の目印。この 1 ファイルの有無だけで判定する
ENTRY_POINT = Path("src") / "quick_slide" / "server" / "__main__.py"

# 会話の識別子を採る環境変数。**ここに定数を足さないこと**（定数を渡すと全部の会話が 1 つに潰れる）
CONVERSATION_KEY_ENV: tuple[tuple[str, str], ...] = (
    ("CLAUDE_CODE_SESSION_ID", "claude_code"),
    ("CODEX_THREAD_ID", "codex"),
    ("CODEX_SESSION_ID", "codex"),
)
# 識別子は採れないがランタイムは分かる場合の手掛かり（台本を書く LLM を会話の出所に合わせるのに使う）
RUNTIME_HINT_ENV: tuple[tuple[str, str], ...] = (
    ("CLAUDECODE", "claude_code"),
    ("CODEX_HOME", "codex"),
    ("CODEX_SANDBOX", "codex"),
)

# サーバが要る Python（SQLite の接続の `autocommit=` が 3.12 から）。古い Python ではサーバが起きる前に落ちる
SERVER_PYTHON_MIN = (3, 12)
PYTHON_TOO_OLD_MESSAGE = (
    "この Python（{version}・{exe}）は古く、Quick Slide のサーバを起こせません（3.12 以上が要ります）。"
    "Windows は `py -3` で、無ければ 3.12 以上の python.exe（利用者ごとに入れたものは %LOCALAPPDATA%\\Programs\\Python\\Python3*\\python.exe）で起こし直してください。"
)
# Codex が会話の中でコマンドを走らせるときに付く印（quick_slide_setup.py と同じ）。どれかがあれば「Codex の中」とみなす
CODEX_MARKERS = ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_THREAD_ID", "CODEX_SESSION_ID")
START_IN_TERMINAL_MESSAGE = (
    "Codex の砂場の中からは、Quick Slide のサーバを起こせません（データの置き場など、作業場の外に書けないため）。"
    "利用者のターミナルで次のコマンドを流してサーバを起こしてから、もう一度頼んでください: {command}"
)
SANDBOX_BLOCKED_MESSAGE = (
    "Quick Slide のサーバに接続できません。いま動いている環境が、手元（127.0.0.1）への通信を塞いでいます。"
    "Codex から使うときは、砂場の設定でネットワークを開けてください（`-c sandbox_workspace_write.network_access=true`）。"
)


class Failure(Exception):
    def __init__(self, code: str, message: str, exit_code: int, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.extra = extra


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


# --- 置き場所 -------------------------------------------------------------

def data_root(explicit: str | None) -> Path:
    """サーバの `paths.py` と同じ既定（`$QUICK_SLIDE_HOME` → `~/.quick-slide`）。"""
    for value in (explicit, os.environ.get(ENV_HOME)):
        if value:
            return Path(value).expanduser().resolve()
    return (Path.home() / ".quick-slide").resolve()


def bundled_repo() -> Path:
    """`skills/quick-slide/scripts/` から 3 つ上（プラグインの根。リポジトリでも配ったものでも同じ形）。"""
    return Path(__file__).resolve().parents[3]


def resolve_repo(explicit: str | None) -> dict[str, Any]:
    candidates = []
    chosen = None
    bad = None
    for source, value in (("--repo", explicit), (ENV_REPO, os.environ.get(ENV_REPO) or None),
                          ("plugin", str(bundled_repo()))):
        entry: dict[str, Any] = {"source": source, "path": value, "state": "unset"}
        if value:
            path = Path(value).expanduser().resolve()
            entry["path"] = str(path)
            entry["state"] = "ok" if (path / ENTRY_POINT).is_file() else "no_entity"
        candidates.append(entry)
        if entry["state"] == "ok" and chosen is None and bad is None:
            chosen = entry
        if entry["state"] == "no_entity" and source != "plugin" and chosen is None and bad is None:
            bad = entry
    return {"repo": chosen["path"] if chosen else None, "source": chosen["source"] if chosen else None,
            "candidates": candidates, "invalid": bad}


def repo_root(explicit: str | None) -> Path:
    r = resolve_repo(explicit)
    if r["invalid"] is not None:
        bad = r["invalid"]
        raise Failure("repo_invalid", f"{bad['source']} が指している場所に Quick Slide の本体（src/quick_slide）がありません: {bad['path']}",
                      EXIT_STARTUP, candidates=r["candidates"])
    if r["repo"] is None:
        raise Failure("repo_not_found", "Quick Slide の本体（src/quick_slide）が見つかりません。プラグインを入れ直すか、"
                      f"{ENV_REPO} でリポジトリを指してください。", EXIT_STARTUP, candidates=r["candidates"])
    return Path(r["repo"])


# --- 1. サーバを起こす ----------------------------------------------------

def _last_json_line(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _product_env(repo: Path) -> dict[str, str]:
    env = os.environ.copy()
    src = str(repo / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"   # サーバとその子を UTF-8 モードで（Windows の既定の cp932 で読み書きしない）
    return env


def _blocked_by_sandbox(home: Path) -> bool:
    """届かない理由が砂場（手元への通信を塞がれている）か。サーバが居ないのとは別に言う。"""
    import errno
    import socket

    try:
        record = json.loads((home / "runtime" / "server.json").read_text(encoding="utf-8"))
        host, port = record["host"], int(record["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    try:
        with socket.create_connection((host, port), timeout=3.0):
            return False
    except OSError as exc:
        return exc.errno in (errno.EPERM, errno.EACCES)


def inside_codex() -> bool:
    return any(os.environ.get(name) for name in CODEX_MARKERS)


def terminal_start_command(home: Path) -> str:
    """利用者のターミナルでサーバを起こすコマンド。砂場の中の Python は古い別物のことがあるので、`py -3`・`python3` で書く。"""
    cmd = ["py", "-3"] if os.name == "nt" else ["python3"]
    cmd += [str(Path(__file__).resolve().with_name("quick_slide_start.py")), "--start-server"]
    if home != (Path.home() / ".quick-slide").resolve():
        cmd += ["--home", str(home)]
    return (subprocess.list2cmdline if os.name == "nt" else shlex.join)(cmd)


def _start_failed(home: Path, code: str, message: str) -> Failure:
    """サーバを起こせなかった。Codex の砂場の中なら、回り道させずに、ターミナルで起こすコマンドを返す。"""
    if inside_codex():
        command = terminal_start_command(home)
        return Failure("start_in_terminal", START_IN_TERMINAL_MESSAGE.format(command=command), EXIT_STARTUP, command=command)
    return Failure(code, message, EXIT_STARTUP)


# --- 古い版のサーバを入れ替える（docs/adr/0020-release-and-update-notice.md）---
# プラグインを新しい版にしても、常駐のサーバは止めるまで前の版のまま動く。新しい版の入口（このスクリプト）が、
# 自分より古い版のサーバを見つけたら、手が空いていれば止めてから起こす（新しい版で起動する）。処理中の作業が
# あれば触らない（古い版のまま使い、次の機会に入れ替える）。Codex の砂場の中では起こし直せないので触らない。
# 自分の方が古いとき（別のアプリに古い版が残っている）は入れ替えない（行ったり来たりさせない）。

_VERSION_RE = re.compile(r'^__version__ = "([^"]+)"$', re.M)
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def product_version(repo: Path) -> str | None:
    try:
        m = _VERSION_RE.search((repo / "src" / "quick_slide" / "__init__.py").read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def _version_key(text: Any) -> tuple | None:
    m = _SEMVER_RE.match(text) if isinstance(text, str) else None
    if not m:
        return None
    core = tuple(int(x) for x in m.groups()[:3])
    if m.group(4) is None:
        return (*core, 1, ())
    return (*core, 0, tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in m.group(4).split(".")))


def is_newer(candidate: Any, than: Any) -> bool:
    a, b = _version_key(candidate), _version_key(than)
    return a is not None and b is not None and a > b


def _running_server(home: Path) -> dict[str, Any] | None:
    """動いているサーバの版と、走っている仕事の数（health）。動いていない・読めなければ None。"""
    try:
        record = json.loads((home / "runtime" / "server.json").read_text(encoding="utf-8"))
        status, body = request(record, "GET", "/api/health", timeout=5.0)
    except (OSError, ValueError, KeyError, TypeError, Failure):
        return None
    if not isinstance(body, dict) or body.get("service") != "quick-slide":
        return None
    worker = body.get("worker") or {}
    return {"version": body.get("version"), "busy": int(worker.get("busy") or 0) if isinstance(worker, dict) else 0}


def replace_outdated_server(repo: Path, home: Path, timeout: float) -> dict[str, Any] | None:
    """自分より古い版のサーバが動いていれば、手が空いているときだけ止める（このあとの `start` が新しい版で起こす）。"""
    mine = product_version(repo)
    if mine is None or inside_codex():
        return None
    running = _running_server(home)
    if running is None or not is_newer(mine, running["version"]):
        return None
    note = {"from": running["version"], "to": mine}
    if running["busy"] > 0:
        return {**note, "replaced": False, "busy": running["busy"]}
    cmd = [sys.executable, "-m", "quick_slide.server", "--home", str(home), "--timeout", str(timeout), "stop"]
    try:
        proc = subprocess.run(cmd, cwd=str(repo), env=_product_env(repo), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout + 20.0, encoding="utf-8")
        stopped = proc.returncode == 0 and bool((_last_json_line(proc.stdout) or {}).get("stopped"))
    except (OSError, subprocess.TimeoutExpired):
        stopped = False
    return {**note, "replaced": stopped}


def ensure_server(repo: Path, home: Path, timeout: float) -> dict[str, Any]:
    """製品の `start` を呼ぶ。既に動いていれば `already_running` で即返る（自分より古い版なら、先に入れ替える）。"""
    swap = replace_outdated_server(repo, home, timeout)
    result = _start(repo, home, timeout)
    if swap is not None:
        result = {**result, "version_swap": swap}
    return result


def _start(repo: Path, home: Path, timeout: float) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "quick_slide.server", "--home", str(home), "--timeout", str(timeout), "start", "--quiet"]
    try:
        proc = subprocess.run(cmd, cwd=str(repo), env=_product_env(repo), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout + 20.0, encoding="utf-8")
    except subprocess.TimeoutExpired:
        raise _start_failed(home, "server_start_timeout", "Quick Slide のサーバを起動できませんでした（応答がありません）。") from None
    except OSError:
        raise _start_failed(home, "server_start_failed",
                            "Quick Slide のサーバを起動できませんでした。Python の場所とアクセス権を確認してください。") from None
    result = _last_json_line(proc.stdout)   # 子の stderr は読まない・出さない
    if proc.returncode != 0 or result is None or not result.get("ok"):
        if _blocked_by_sandbox(home):
            raise Failure("server_unreachable", SANDBOX_BLOCKED_MESSAGE, EXIT_STARTUP)
        # Codex の中なら、ターミナルで起こしてもらうのが先（砂場の中の Python は、古い別物のことがある）
        if sys.version_info[:2] < SERVER_PYTHON_MIN and not inside_codex():
            v = sys.version_info
            raise Failure("python_too_old", PYTHON_TOO_OLD_MESSAGE.format(version=f"{v.major}.{v.minor}.{v.micro}", exe=sys.executable),
                          EXIT_STARTUP)
        message = "Quick Slide のサーバを起動できませんでした。"
        if isinstance(result, dict) and isinstance(result.get("message"), str):
            message = result["message"]
        raise _start_failed(home, "server_unavailable", message)
    return result


def read_record(home: Path) -> dict[str, Any]:
    try:
        record = json.loads((home / "runtime" / "server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Failure("server_record_unreadable", "起動しているサーバの情報を読めませんでした。もう一度お試しください。",
                      EXIT_STARTUP) from None
    if not isinstance(record, dict) or not all(k in record for k in ("host", "port", "url", "session_token")):
        raise Failure("server_record_unreadable", "起動しているサーバの情報を読めませんでした。もう一度お試しください。", EXIT_STARTUP)
    return record


# --- 2. HTTP -------------------------------------------------------------

def request(record: dict[str, Any], method: str, path: str, body: dict[str, Any] | None = None,
            *, session: bool = False, timeout: float = 60.0, raw_body: bytes | None = None,
            body_file: Path | None = None, extra_headers: dict[str, str] | None = None,
            want_bytes: bool = False) -> tuple[int, Any]:
    """want_bytes: 200 のときの本文を JSON として読まず、そのままの bytes で返す（Markdown の書き出しなど）。"""
    host, port = str(record["host"]), int(record["port"])
    headers = {"Host": f"[{host}]:{port}" if ":" in host else f"{host}:{port}", "Accept": "application/json"}
    headers.update(extra_headers or {})
    raw: Any = b""
    if body_file is not None:
        # 大きな本文（取り込む PPTX）はファイルから流す（メモリに読み込まない）
        raw = body_file.open("rb")
        headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        headers["Content-Length"] = str(body_file.stat().st_size)
    elif raw_body is not None:
        raw = raw_body
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(len(raw))
    elif body is not None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(raw))
    if session:
        headers[SESSION_HEADER] = str(record["session_token"])
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(method, path, body=raw or None, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        status = response.status
    except (OSError, http.client.HTTPException):
        raise Failure("server_unreachable", "Quick Slide のサーバに接続できませんでした。もう一度お試しください。", EXIT_STARTUP) from None
    finally:
        conn.close()
        if body_file is not None:
            raw.close()
    if want_bytes and status == 200:
        return status, payload
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except ValueError:
        parsed = None
    return status, parsed


def api_failure(status: int, body: Any) -> Failure:
    """サーバの日本語の文と code をそのまま運ぶ（言い換えない）。"""
    error = body.get("error") if isinstance(body, dict) else None
    code, message, details = "http_error", "Quick Slide が要求を受け付けませんでした。", {}
    if isinstance(error, dict):
        code = str(error.get("code") or code)
        if isinstance(error.get("message"), str):
            message = error["message"]
        if isinstance(error.get("details"), dict):
            details = error["details"]   # パターン名・項目名だけ（一致した文字列は返らない）
    exit_code = EXIT_INPUT if status in (400, 404, 422) else EXIT_STARTUP if status in (401, 403, 503) else EXIT_FAILED
    return Failure(code, message, exit_code, http_status=status, details=details)


def detect_conversation() -> tuple[str, str | None, str | None]:
    """(runtime, 会話の識別子, 採った環境変数名)。**入れ子（印が 2 種類立っている）なら推測せず渡さない。**"""
    seen = {runtime for name, runtime in CONVERSATION_KEY_ENV + RUNTIME_HINT_ENV if (os.environ.get(name) or "").strip()}
    if len(seen) != 1:
        return "other", None, None
    here = seen.pop()
    for name, runtime in CONVERSATION_KEY_ENV:
        value = (os.environ.get(name) or "").strip()
        if runtime == here and value:
            return runtime, value, name
    return here, None, None


def read_text_file(path: str, label: str) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise Failure("file_unreadable", f"{label}のファイルを UTF-8 テキストとして読めませんでした。", EXIT_INPUT) from None
