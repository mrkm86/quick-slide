#!/usr/bin/env python3
"""会話から整えた原稿を Quick Slide に投入し、スライドの画面の URL を返す（会話の入口）。

やること（この順で。人に「起動してください」と言わない）:

1. 常駐サーバの状態を見て、落ちていれば起こして health を待つ。**ここは再実装しない**。製品の CLI
   （`python -m quick_slide.server --home <置き場> start`）を子プロセスとして呼ぶ（二重起動はサーバの錠が拒む）
2. `runtime/server.json`（0600）から URL とセッショントークンを読む
3. 原稿を `POST /api/intakes` に投げる（台本係が台本を書き、エンジンが描く。進みは画面が実況する）
4. 応答の `url`・`deck_id`・`launch_id` などを JSON 1 行で出す

画面の開き方は 3 段（`--ensure-launch`）: 会話しているアプリが内蔵ブラウザで URL を開く → 開いた合図（attach）が
サーバに届くのを待つ → 来なければ外部ブラウザで開く → それでも来なければ URL を案内する。URL はいつも会話に残す。

**実体（サーバのコード）の探し方**: `--repo` → 環境変数 `QUICK_SLIDE_REPO` → このスキルを載せたプラグインの根
（`skills/quick-slide/scripts/` から 3 つ上）。指されているのに実体が無ければ、黙って次へ落ちずにその場で止まる。

出力: **stdout の最終行は JSON 1 行**。終了コードは 0=成功 / 2=入力の誤り / 3=起動できない・届かない / 4=処理の失敗。

秘匿: 原稿本文・会話の識別子の生値を stdout / stderr / ファイルに書かない（JSON に出すのは文字数と
「識別子をどの環境変数から採ったか」の名前だけ）。子プロセスの stderr は読まない（原稿が混ざりうる）。

標準ライブラリだけを使う（サーバ本体を import しない）。
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import sys
import webbrowser
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

MODES = ("presentation", "pitch")
STYLES = {"presentation": ("standard", "visual"), "pitch": ("visual", "standard", "infographic")}
IMAGE_LOOKS = ("photo", "illustration", "handdrawn")

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

NOT_YET_MESSAGE = "{what}は、この版の Quick Slide ではまだできません。スライドの画面か、新しいスライドとして頼んでください。"
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


def ensure_server(repo: Path, home: Path, timeout: float) -> dict[str, Any]:
    """製品の `start` を呼ぶ。既に動いていれば `already_running` で即返る。"""
    cmd = [sys.executable, "-m", "quick_slide.server", "--home", str(home), "--timeout", str(timeout), "start", "--quiet"]
    try:
        proc = subprocess.run(cmd, cwd=str(repo), env=_product_env(repo), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout + 20.0)
    except subprocess.TimeoutExpired:
        raise Failure("server_start_timeout", "Quick Slide のサーバを起動できませんでした（応答がありません）。", EXIT_STARTUP) from None
    except OSError:
        raise Failure("server_start_failed", "Quick Slide のサーバを起動できませんでした。Python の場所とアクセス権を確認してください。",
                      EXIT_STARTUP) from None
    result = _last_json_line(proc.stdout)   # 子の stderr は読まない・出さない
    if proc.returncode != 0 or result is None or not result.get("ok"):
        if _blocked_by_sandbox(home):
            raise Failure("server_unreachable", SANDBOX_BLOCKED_MESSAGE, EXIT_STARTUP)
        message = "Quick Slide のサーバを起動できませんでした。"
        if isinstance(result, dict) and isinstance(result.get("message"), str):
            message = result["message"]
        raise Failure("server_unavailable", message, EXIT_STARTUP)
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
            *, session: bool = False, timeout: float = 60.0) -> tuple[int, Any]:
    host, port = str(record["host"]), int(record["port"])
    headers = {"Host": f"[{host}]:{port}" if ":" in host else f"{host}:{port}", "Accept": "application/json"}
    raw = b""
    if body is not None:
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


def browser_mode(record: dict[str, Any]) -> str:
    override = (os.environ.get("QUICK_SLIDE_BROWSER") or "").strip()
    if override in ("auto", "external", "manual"):
        return override
    try:
        status, body = request(record, "GET", "/api/health", timeout=10.0)
    except Failure:
        return "auto"
    mode = ((body or {}).get("browser") or {}).get("mode") if status == 200 and isinstance(body, dict) else None
    return mode if mode in ("auto", "external", "manual") else "auto"


# --- 3. payload ----------------------------------------------------------

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


def build_payload(args: argparse.Namespace) -> tuple[dict[str, Any], str | None]:
    runtime, key, key_source = detect_conversation()
    if args.runtime:
        runtime = args.runtime
    origin: dict[str, Any] = {"kind": "conversation", "runtime": runtime}
    if args.conversation_key:
        origin["conversation_key"] = args.conversation_key
        key_source = "--conversation-key"
    elif key is not None:
        origin["conversation_key"] = key
    else:
        key_source = None
    if not args.manuscript_file:
        raise Failure("manuscript_required", "原稿がありません。会話から整えた原稿を --manuscript-file で渡してください。", EXIT_INPUT)
    manuscript = read_text_file(args.manuscript_file, "原稿")
    if not manuscript.strip():
        raise Failure("manuscript_required", "原稿が空です。", EXIT_INPUT)
    attrs: dict[str, Any] = {"mode": args.mode}
    if args.style:
        if args.style not in STYLES[args.mode]:
            raise Failure("style_invalid", f"--style はこのモードでは {' / '.join(STYLES[args.mode])} のいずれかです。", EXIT_INPUT)
        attrs["style"] = args.style
    for name in ("purpose", "audience", "style_note", "image_look", "brand_id"):
        value = getattr(args, name)
        if value:
            attrs[name] = value
    if args.pages is not None:
        attrs["page_count"] = args.pages
    if args.no_images:
        attrs["images"] = False
    payload: dict[str, Any] = {"schema_version": 1, "origin": origin, "manuscript": manuscript, "deck_attributes": attrs}
    if args.title:
        payload["title_hint"] = args.title
    if args.new_deck:
        payload["force_new_deck"] = True
    return payload, key_source


def payload_summary(payload: dict[str, Any], key_source: str | None) -> dict[str, Any]:
    """**本文を出さない**要約（文字数と選んだ値だけ）。"""
    return {
        "manuscript_chars": len(payload["manuscript"]),
        "title_hint": payload.get("title_hint"),
        "deck_attributes": payload.get("deck_attributes") or {},
        "force_new_deck": bool(payload.get("force_new_deck")),
        "origin": {"kind": payload["origin"]["kind"], "runtime": payload["origin"]["runtime"],
                   "conversation_key_present": "conversation_key" in payload["origin"],
                   "conversation_key_source": key_source},
    }


def conversation_message(body: dict[str, Any]) -> str:
    title = body.get("deck_title") or "無題のスライド"
    if body.get("resolution") == "resent":
        return f"同じ内容をさっき受け付けたスライド「{title}」を開きます。"
    mode = "プレゼン（編集できて動く PPTX）" if body.get("mode") == "presentation" else "ピッチ（1 枚ずつ絵で描く資料）"
    return f"新しいスライド「{title}」を{mode}で作り始めました。台本を書いて描き終えると、画面にページが出ます。"


# --- 実行 ---------------------------------------------------------------

def cmd_ensure_launch(repo: Path, home: Path, args: argparse.Namespace) -> int:
    """開けたかを確かめ、開けていなければ外部ブラウザへ（待つ秒数も順序も製品の `launch ensure` が決める）。"""
    if not args.url:
        raise Failure("url_required", "--ensure-launch には --url（投入で返った URL）も渡してください。", EXIT_INPUT)
    ensure_server(repo, home, args.server_timeout)
    cmd = [sys.executable, "-m", "quick_slide.server", "--home", str(home), "launch", "ensure", args.ensure_launch,
           "--url", args.url]
    if args.browser_mode:
        cmd += ["--mode", args.browser_mode]
    try:
        proc = subprocess.run(cmd, cwd=str(repo), env=_product_env(repo), stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=args.server_timeout + 120.0)
    except (subprocess.TimeoutExpired, OSError):
        raise Failure("launch_ensure_failed", "ブラウザで開けたか確認できませんでした。この URL をブラウザで開いてください。",
                      EXIT_STARTUP) from None
    result = _last_json_line(proc.stdout)
    if result is None:
        raise Failure("launch_ensure_failed", "ブラウザで開けたか確認できませんでした。この URL をブラウザで開いてください。", EXIT_STARTUP)
    if not result.get("ok"):
        raise Failure(str(result.get("code") or "launch_ensure_failed"),
                      str(result.get("message") or "ブラウザで開けたか確認できませんでした。"),
                      EXIT_STARTUP if proc.returncode == EXIT_STARTUP else EXIT_INPUT)
    emit({"status": "ensured", **{k: v for k, v in result.items() if k != "ok"}})
    return EXIT_OK


def cmd_where(args: argparse.Namespace, home: Path) -> int:
    r = resolve_repo(args.repo)
    emit({"status": "where", "home": str(home), "repo": r["repo"], "source": r["source"], "candidates": r["candidates"],
          "skill_dir": str(Path(__file__).resolve().parents[1])})
    return EXIT_OK if r["repo"] else EXIT_STARTUP


def run(args: argparse.Namespace) -> int:
    home = data_root(args.home)
    if args.where:
        return cmd_where(args, home)
    for flag, what in ((args.revise, "会話からの修正（--revise）"), (args.append_to_deck, "既存のスライドへの追加（--append-to-deck）"),
                       (args.attach, "画像の添付（--attach）"), (args.transcript_file, "会話抜粋（--transcript-file）")):
        if flag:
            raise Failure("not_ready_in_v2", NOT_YET_MESSAGE.format(what=what), EXIT_INPUT)
    repo = repo_root(args.repo)
    if args.ensure_launch:
        return cmd_ensure_launch(repo, home, args)
    payload, key_source = build_payload(args)
    started = ensure_server(repo, home, args.server_timeout)
    record = read_record(home)
    server = {"url": record.get("url"), "pid": started.get("pid"), "already_running": bool(started.get("already_running"))}
    if args.dry_run:
        emit({"status": "dry_run", "server": server, "payload": payload_summary(payload, key_source)})
        return EXIT_OK
    status, body = request(record, "POST", "/api/intakes", payload, session=True, timeout=120.0)
    if status != 201 or not isinstance(body, dict):
        raise api_failure(status, body)
    # 既定では開かない。内蔵ブラウザで開けるのは会話しているアプリだけで、起動のトークンは 1 回しか使えないため、
    # ここで外部ブラウザを開くと内蔵ブラウザの経路が潰れる。`browser.mode=external` か `--open` のときだけ開く
    mode = browser_mode(record)
    should_open = False if (args.no_open or mode == "manual") else (args.open_browser or mode == "external")
    opened = bool(webbrowser.open(body["url"])) if should_open else False
    emit({
        "status": "ready", "url": body["url"], "deck_id": body.get("deck_id"), "deck_title": body.get("deck_title"),
        "mode": body.get("mode"), "style": body.get("style"), "resolution": body.get("resolution"),
        "intake_id": body.get("intake_id"), "launch_id": body.get("launch_id"), "job_id": body.get("job_id"),
        "message": conversation_message(body), "opened": opened, "browser_mode": mode, "server": server,
        "payload": payload_summary(payload, key_source),
    })
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quick_slide_start.py",
                                description="会話から整えた原稿を Quick Slide に投入し、スライドの画面の URL を返す")
    p.add_argument("--manuscript-file", help="原稿（Markdown, UTF-8）のファイル")
    p.add_argument("--title", help="スライドの仮の題（title_hint）")
    p.add_argument("--mode", choices=MODES, default="presentation",
                   help="presentation（既定・編集できて動く PPTX）／pitch（1 枚ずつ画像生成で丸ごと描く）。あとから替えられない")
    p.add_argument("--style", help="presentation: standard（既定）/ visual。pitch: visual（既定）/ standard / infographic")
    p.add_argument("--image-look", choices=IMAGE_LOOKS, help="絵の画風（資料全体で 1 つ）。省けばスタイルが決める")
    p.add_argument("--no-images", action="store_true", help="画像の枠を置かない（図と文字だけで組む）")
    p.add_argument("--purpose", help="目的")
    p.add_argument("--audience", help="想定の読み手")
    p.add_argument("--style-note", help="作風の一言")
    p.add_argument("--pages", type=int, help="希望の枚数")
    p.add_argument("--brand-id", help="ブランドの UUID（設定の画面で登録したもの）")
    p.add_argument("--conversation-key", help="会話の識別子を明示する（既定は環境変数から。定数は渡さない）")
    p.add_argument("--runtime", choices=("claude_code", "codex", "browser", "cli", "other"),
                   help="origin.runtime（既定は環境変数から。台本を書く LLM をこれに合わせる）")
    p.add_argument("--new-deck", action="store_true", help="同じ内容の送り直しでも、必ず新しいスライドにする")
    p.add_argument("--ensure-launch", metavar="launch_id", help="URL を見せたあとに呼ぶ。開けていなければ外部ブラウザへ（--url も渡す）")
    p.add_argument("--url", help="--ensure-launch に渡す、投入で返った URL")
    p.add_argument("--browser-mode", choices=("auto", "external", "manual"), help="--ensure-launch の経路を 1 回だけ上書きする")
    p.add_argument("--open", dest="open_browser", action="store_true", help="外部ブラウザでも URL を開く")
    p.add_argument("--no-open", action="store_true", help="自動で開かない（既定の挙動を明示するとき）")
    p.add_argument("--dry-run", action="store_true", help="投入せず、サーバの起動と payload の要約だけ出す")
    p.add_argument("--where", action="store_true", help="どこを実体（src/quick_slide）として見ているかを出す（サーバは起こさない）")
    p.add_argument("--home", help=f"データの置き場（既定: ${ENV_HOME} または ~/.quick-slide）")
    p.add_argument("--repo", help=f"実体のあるリポジトリ（この実行だけ。{ENV_REPO} より優先）")
    p.add_argument("--server-timeout", type=float, default=30.0, help="サーバの起動を待つ秒数")
    # この版ではまだできないこと（呼ばれたら「まだできない」とはっきり答える）
    p.add_argument("--revise", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--append-to-deck", help=argparse.SUPPRESS)
    p.add_argument("--attach", action="append", help=argparse.SUPPRESS)
    p.add_argument("--transcript-file", help=argparse.SUPPRESS)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except Failure as failure:
        emit({"status": "error", "code": failure.code, "message": failure.message, **failure.extra})
        return failure.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
