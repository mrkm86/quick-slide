"""台本係の LLM を CLI で 1 回呼ぶ（Claude Code の `claude -p`・Codex の `codex exec`）。**サブスク枠だけ**。

- 子の環境は `childenv.child_env`（API キーを消す）。指示文（原稿入り）は標準入力で渡し、ファイルに書かない
- 道具を使わせない: `claude` は道具を空にし（`--tools ""`）、MCP・スキル・カスタマイズを読ませない。
  `codex` は読み取りだけの砂場（`-s read-only`）・承認を求めない（`-a never`）・利用者の設定を読まない・
  セッションを残さない。作業の根は描いた回の `writer/` の空のフォルダ
- 受信したイベントは各呼び出しの `events.jsonl` に逐次保存する。途中の本文は採用せず、最終結果だけを読む
- 応答から JSON のオブジェクトを 1 つだけ取り出す（説明やコードフェンスは許す。壊れた JSON は直さない）
- 下調べ係（`writer/research.py`）も同じ CLI を同じ旗で呼び、**Web の検索と取得だけ**を足す（`web=True`）:
  `claude` は道具を WebSearch・WebFetch だけにして、その 2 つだけを前もって許し、ほかは聞かずに断る（`--permission-mode dontAsk`）。
  `codex` は Web 検索を開き（`--search`・`web_search="live"`）、シェル・ブラウザ・コンピュータ操作の道具を切る
  （読み取りだけの砂場でもシェルはディスクを読めるので、Web と一緒に持たせると外へ漏らせてしまう）。
  手元の CLI が絞り方を知らなければ、絞れないまま呼ばずに失敗する

失敗は `WriterFailed(kind, message)`。`kind` は auth / rate / missing / timeout / stopped / failed / format。
`message` は画面と会話にそのまま出す日本語。
"""

from __future__ import annotations

import json
import re
import threading
from functools import wraps
from pathlib import Path
from typing import Any

from ..worker.proc import run_process

BACKENDS = ("claude", "codex")
RUNTIME_TO_BACKEND = {"claude_code": "claude", "codex": "codex"}

_RATE = re.compile(r"usage\s+limit|rate[\s_-]*limit|limit\s+reached|\b429\b|try again (?:at|in)|quota", re.I)
_AUTH = re.compile(r"not logged in|log\s?in required|please (?:run )?/?log\s?in|unauthori[sz]ed|authenticat|\b401\b|"
                   r"invalid api key|oauth token|credential", re.I)

LOGIN_HINT = {
    "claude": "台本を書く Claude Code にログインできていません。ターミナルで `claude` を起動して /login してから、もう一度頼んでください。",
    "codex": "台本を書く Codex にログインできていません。ターミナルで `codex login`（ChatGPT でログイン）をしてから、もう一度頼んでください。",
}
RATE_HINT = {
    "claude": "台本を書く Claude Code の利用枠に達しました。時間をおいてから、もう一度頼んでください。",
    "codex": "台本を書く Codex の利用枠に達しました。時間をおいてから、もう一度頼んでください。",
}
NAMES = {"claude": "Claude Code", "codex": "Codex"}
INSTALL_HINT = {
    "claude": "Claude Code（`claude`）を入れてサブスクでログインし",
    "codex": "Codex（`codex`）を入れて `codex login`（ChatGPT でログイン）し",
}

# `claude -p` に付けたい旗。**手元の claude が知らない旗は付けない**（版によって無い。`--help` を読んで決める）
CLAUDE_WANTED_FLAGS = (
    ("--safe-mode",),                    # CLAUDE.md・フック・スキル・プラグイン・MCP を読ませない（認証は普段どおり）
    ("--tools", ""),                     # 道具を 1 つも使わせない
    ("--strict-mcp-config",),            # MCP は --mcp-config で渡したものだけ（渡さない＝無し）
    ("--disable-slash-commands",),       # スキルを使わせない
    ("--no-session-persistence",),       # セッションを残さない
)
_claude_help_cache: dict[str, str] = {}
_help_lock = threading.Lock()
# 下調べ係に許す道具（Claude Code の組み込みの名前）
WEB_TOOLS = "WebSearch,WebFetch"
# 下調べ係の codex で切る道具（`codex features list` の名前）。読み取りだけの砂場でもディスクは読めるので、
# 手元のものを読める道具（シェル・画像を見る・ブラウザ・アプリ・プラグイン・下請け）は全部切り、Web の検索だけを残す
# （画像を見る道具が残っていると、Web の中の指示で手元の画像を読み、検索語に混ぜて外へ出せる。2026-10-12 の検分）
CODEX_WEB_DISABLED_FEATURES = ("shell_tool", "unified_exec", "view_image", "image_generation", "browser_use",
                               "browser_use_external", "browser_use_full_cdp_access", "computer_use", "in_app_browser",
                               "in_app_local_automation", "apps", "plugins", "remote_plugin", "multi_agent",
                               "code_mode_host", "skill_mcp_dependency_install")
_codex_help_cache: dict[str, str] = {}


class WriterFailed(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message



# 資料ごとの場面並列とは別に、台本係・下調べ係のCLIの総数を抑える。
MAX_CONCURRENT_CLI = 8
_cli_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CLI)


def _limit_cli(runner):
    @wraps(runner)
    def limited(*args, **kwargs):
        stop = kwargs.get("stop")
        while not _cli_slots.acquire(timeout=0.1):
            if stop is not None and stop.is_set():
                raise WriterFailed("stopped", "サーバを止めたので、台本を書くのを中断しました。")
        try:
            if stop is not None and stop.is_set():
                raise WriterFailed("stopped", "サーバを止めたので、台本を書くのを中断しました。")
            return runner(*args, **kwargs)
        finally:
            _cli_slots.release()
    return limited


def choose_backend(setting: str, origin_runtime: str | None, available: dict[str, str | None]) -> tuple[str, str]:
    """どの LLM で書くか。(backend, 理由)。

    - 設定で `claude` / `codex` に固定 → それ。無ければ失敗（**黙って別の CLI に替えない**）
    - `auto` で会話の出所が分かる（Claude Code / Codex）→ 同じものを優先。無ければ手元にあるもう一方
    - `auto` で出所が分からない（画面・CLI など）→ 手元にあるものを claude → codex の順で
    ログインできているかは呼んだときに分かる（失敗の文が `claude` の /login・`codex login` を言う）。
    """
    if setting in BACKENDS:
        if not available.get(setting):
            raise WriterFailed("missing", f"台本を書く {NAMES[setting]} の CLI が見つかりません（設定で {setting} に固定されています）。"
                                          f"{INSTALL_HINT[setting]}てください。または、設定の writer を auto に戻してください。")
        return setting, "settings"
    preferred = RUNTIME_TO_BACKEND.get(origin_runtime or "")
    if preferred and available.get(preferred):
        return preferred, "origin"
    for name in BACKENDS:
        if available.get(name):
            return name, "available"
    raise WriterFailed("missing", "台本を書く CLI（claude か codex）が見つかりません。どちらかを入れて、サブスクでログインしてください。")


def extract_json(text: str) -> dict[str, Any]:
    """説明やコードフェンスは許すが、壊れた JSON の修復・2 つ目への逃げはしない。"""
    def unique(pairs):
        out: dict[str, Any] = {}
        for k, v in pairs:
            if k in out:
                raise ValueError("JSON のキーが重複")
            out[k] = v
        return out

    try:
        start = re.search(r"\{", text)
        if start is None:
            raise ValueError("JSON が無い")
        decoder = json.JSONDecoder(object_pairs_hook=unique)
        value, end = decoder.raw_decode(text, start.start())
        rest = text[end:]
        if not isinstance(value, dict) or re.search(r"[\[{]", rest):
            raise ValueError("オブジェクト 1 つではない")
        return value
    except (ValueError, TypeError, RecursionError):
        raise WriterFailed("format", "台本の JSON を読めませんでした") from None


def _classify(backend: str, text: str) -> WriterFailed | None:
    if _AUTH.search(text):
        return WriterFailed("auth", LOGIN_HINT[backend])
    if _RATE.search(text):
        return WriterFailed("rate", RATE_HINT[backend])
    return None


def _help_text(cache: dict[str, str], cmd: list[str], env: dict[str, str], cwd: Path) -> str:
    with _help_lock:
        help_text = cache.get(cmd[0])
        if help_text is None:
            r = run_process(cmd, cwd=cwd, env=env, timeout=30)
            help_text = r.stdout + "\n".join(r.stderr_tail)
            cache[cmd[0]] = help_text
    return help_text


def _knows(help_text: str, flag: str) -> bool:
    return re.search(re.escape(flag) + r"\b", help_text) is not None


def _claude_flags(exe: str, env: dict[str, str], cwd: Path, *, web: bool = False) -> list[str]:
    help_text = _help_text(_claude_help_cache, [exe, "--help"], env, cwd)
    flags: list[str] = []
    for spec in CLAUDE_WANTED_FLAGS:
        if web and spec[0] == "--tools":
            continue
        if _knows(help_text, spec[0]):
            flags.extend(spec)
    if web:
        # 道具を Web の 2 つに絞れない版では呼ばない（絞らずに呼ぶと、ファイルやシェルの道具も持ってしまう）
        if not (_knows(help_text, "--tools") and _knows(help_text, "--allowedTools")):
            raise WriterFailed("missing", "下調べをする Claude Code が古く、Web の検索と取得だけに道具を絞れません。"
                                          "Claude Code を新しくしてから、もう一度頼んでください。")
        flags += ["--tools", WEB_TOOLS, "--allowedTools", WEB_TOOLS]
        if _knows(help_text, "--permission-mode") and "dontAsk" in help_text:
            flags += ["--permission-mode", "dontAsk"]
    return flags


@_limit_cli
def ask_claude(exe: str, prompt: str, *, job_dir: Path, env: dict[str, str], timeout: float,
               stop: threading.Event | None, web: bool = False) -> str:
    job_dir.mkdir(parents=True, exist_ok=True)
    flags = _claude_flags(exe, env, job_dir, web=web)
    help_text = _help_text(_claude_help_cache, [exe, "--help"], env, job_dir)
    if not all(_knows(help_text, flag) for flag in ("--verbose", "--include-partial-messages")):
        raise WriterFailed("missing", "台本を書く Claude Code が途中経過の出力に対応していません。"
                                      "Claude Code を新しくしてから、もう一度頼んでください。")
    cmd = [exe, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages", *flags]
    r = run_process(cmd, cwd=job_dir, env=env, timeout=timeout, stop=stop, stdin_text=prompt,
                    stdout_path=job_dir / "events.jsonl", stderr_path=job_dir / "stderr.txt")
    _raise_for_process(r, "claude")
    answer = None
    try:
        for line in r.stdout.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if isinstance(event, dict) and event.get("type") == "result":
                answer = event
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        failure = _classify("claude", r.stdout + "\n".join(r.stderr_tail))
        raise failure or WriterFailed("failed", "台本を書く Claude Code の応答を読めませんでした。")
    text = answer.get("result")
    if answer.get("is_error") or r.rc != 0 or not isinstance(text, str):
        failure = _classify("claude", json.dumps(answer, ensure_ascii=False) + "\n".join(r.stderr_tail))
        raise failure or WriterFailed("failed", "台本を書く Claude Code が途中で止まりました。もう一度頼んでください。")
    return text


def _codex_command(exe: str, job_dir: Path, *, web: bool = False) -> list[str]:
    flags: list[str] = []
    settings = ["features.plugins=false", "features.hooks=false", "features.apps=false", 'forced_login_method="chatgpt"']
    if web:
        settings += [f"features.{name}=false" for name in CODEX_WEB_DISABLED_FEATURES] + ['web_search="live"']
    for setting in settings:
        flags += ["-c", setting]
    if web:
        flags.append("--search")
    return [exe, *flags, "-a", "never", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
            "-s", "read-only", "-C", str(job_dir), "--color", "never", "--json",
            "-o", str(job_dir / "last-message.txt"), "-"]


@_limit_cli
def ask_codex(exe: str, prompt: str, *, job_dir: Path, env: dict[str, str], timeout: float,
              stop: threading.Event | None, web: bool = False) -> str:
    job_dir.mkdir(parents=True, exist_ok=True)
    if web and not _knows(_help_text(_codex_help_cache, [exe, "--help"], env, job_dir), "--search"):
        # 検索できないまま呼ぶと、記憶だけで原稿を起こしてしまう（黙って記憶で書かせない）
        raise WriterFailed("missing", "下調べをする Codex が Web 検索（--search）に対応していません。"
                                      "Codex を新しくするか、設定の writer を claude にしてから、もう一度頼んでください。")
    status = run_process([exe, "login", "status"], cwd=job_dir, env=env, timeout=30, stop=stop)
    said = status.stdout + "\n".join(status.stderr_tail)
    if status.rc != 0 or "Logged in using ChatGPT" not in said:
        raise WriterFailed("auth", LOGIN_HINT["codex"])
    last = job_dir / "last-message.txt"
    last.unlink(missing_ok=True)
    r = run_process(_codex_command(exe, job_dir, web=web), cwd=job_dir, env=env, timeout=timeout, stop=stop,
                    stdin_text=prompt, stdout_path=job_dir / "events.jsonl", stderr_path=job_dir / "stderr.txt")
    _raise_for_process(r, "codex")
    errors = []
    message = None
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") in ("error", "turn.failed"):
            errors.append(json.dumps(ev, ensure_ascii=False))
        item = ev.get("item")
        if ev.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            message = item.get("text")
    if r.rc != 0 or errors:
        failure = _classify("codex", "\n".join(errors + r.stderr_tail))
        raise failure or WriterFailed("failed", "台本を書く Codex が途中で止まりました。もう一度頼んでください。")
    if last.is_file():
        message = last.read_text(encoding="utf-8", errors="replace")
    if not isinstance(message, str) or not message.strip():
        raise WriterFailed("failed", "台本を書く Codex から答えが返りませんでした。もう一度頼んでください。")
    return message


def _raise_for_process(r, backend: str) -> None:
    if r.launch_error:
        raise WriterFailed("missing", f"台本を書く {NAMES[backend]} の CLI を起動できませんでした。")
    if r.stopped:
        raise WriterFailed("stopped", "サーバを止めたので、台本を書くのを中断しました。")
    if r.timed_out:
        raise WriterFailed("timeout", f"台本を書く {NAMES[backend]} が時間内に終わりませんでした。もう一度頼んでください。")


def ask(backend: str, exe: str, prompt: str, *, job_dir: Path, env: dict[str, str], timeout: float,
        stop: threading.Event | None = None) -> dict[str, Any]:
    """1 回書かせて、台本の JSON（オブジェクト）を返す。"""
    runner = ask_claude if backend == "claude" else ask_codex
    return extract_json(runner(exe, prompt, job_dir=job_dir, env=env, timeout=timeout, stop=stop))
