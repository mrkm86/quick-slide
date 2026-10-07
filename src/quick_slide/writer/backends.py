"""台本係の LLM を CLI で 1 回呼ぶ（Claude Code の `claude -p`・Codex の `codex exec`）。**サブスク枠だけ**。

- 子の環境は `childenv.child_env`（API キーを消す）。指示文（原稿入り）は標準入力で渡し、ファイルに書かない
- 道具を使わせない: `claude` は道具を空にし（`--tools ""`）、MCP・スキル・カスタマイズを読ませない。
  `codex` は読み取りだけの砂場（`-s read-only`）・承認を求めない（`-a never`）・利用者の設定を読まない・
  セッションを残さない。作業の根は描いた回の `writer/` の空のフォルダ
- 応答から JSON のオブジェクトを 1 つだけ取り出す（説明やコードフェンスは許す。壊れた JSON は直さない）

失敗は `WriterFailed(kind, message)`。`kind` は auth / rate / missing / timeout / stopped / failed / format。
`message` は画面と会話にそのまま出す日本語。
"""

from __future__ import annotations

import json
import re
import threading
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
_claude_help_lock = threading.Lock()


class WriterFailed(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def choose_backend(setting: str, origin_runtime: str | None, available: dict[str, str | None]) -> tuple[str, str]:
    """どの LLM で書くか。(backend, 理由)。

    - 設定で `claude` / `codex` に固定 → それ。無ければ失敗（**黙って別の CLI に替えない**）
    - `auto` で会話の出所が分かる（Claude Code / Codex）→ 同じもの。無ければ失敗（入れる・ログインするものを言う）
    - `auto` で出所が分からない（画面・CLI など）→ 手元にあるものを claude → codex の順で
    ログインできているかは呼んだときに分かる（失敗の文が `claude` の /login・`codex login` を言う）。
    """
    if setting in BACKENDS:
        if not available.get(setting):
            raise WriterFailed("missing", f"台本を書く {NAMES[setting]} の CLI が見つかりません（設定で {setting} に固定されています）。"
                                          f"{INSTALL_HINT[setting]}てください。または、設定の writer を auto に戻してください。")
        return setting, "settings"
    preferred = RUNTIME_TO_BACKEND.get(origin_runtime or "")
    if preferred:
        if not available.get(preferred):
            raise WriterFailed("missing", f"この会話の {NAMES[preferred]} の CLI が見つからないので、台本を書けません。"
                                          f"{INSTALL_HINT[preferred]}てから、もう一度頼んでください。")
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


def _claude_flags(exe: str, env: dict[str, str], cwd: Path) -> list[str]:
    with _claude_help_lock:
        help_text = _claude_help_cache.get(exe)
        if help_text is None:
            r = run_process([exe, "--help"], cwd=cwd, env=env, timeout=30)
            help_text = r.stdout + "\n".join(r.stderr_tail)
            _claude_help_cache[exe] = help_text
    flags: list[str] = []
    for spec in CLAUDE_WANTED_FLAGS:
        if re.search(re.escape(spec[0]) + r"\b", help_text):
            flags.extend(spec)
    return flags


def ask_claude(exe: str, prompt: str, *, job_dir: Path, env: dict[str, str], timeout: float,
               stop: threading.Event | None) -> str:
    job_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-p", "--output-format", "json", *_claude_flags(exe, env, job_dir)]
    r = run_process(cmd, cwd=job_dir, env=env, timeout=timeout, stop=stop, stdin_text=prompt,
                    stderr_path=job_dir / "stderr.txt")
    _raise_for_process(r, "claude")
    try:
        answer = json.loads(r.stdout)
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


def _codex_command(exe: str, job_dir: Path) -> list[str]:
    flags: list[str] = []
    for setting in ("features.plugins=false", "features.hooks=false", "features.apps=false",
                    'forced_login_method="chatgpt"'):
        flags += ["-c", setting]
    return [exe, *flags, "-a", "never", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
            "-s", "read-only", "-C", str(job_dir), "--color", "never", "--json",
            "-o", str(job_dir / "last-message.txt"), "-"]


def ask_codex(exe: str, prompt: str, *, job_dir: Path, env: dict[str, str], timeout: float,
              stop: threading.Event | None) -> str:
    job_dir.mkdir(parents=True, exist_ok=True)
    status = run_process([exe, "login", "status"], cwd=job_dir, env=env, timeout=30, stop=stop)
    said = status.stdout + "\n".join(status.stderr_tail)
    if status.rc != 0 or "Logged in using ChatGPT" not in said:
        raise WriterFailed("auth", LOGIN_HINT["codex"])
    last = job_dir / "last-message.txt"
    last.unlink(missing_ok=True)
    r = run_process(_codex_command(exe, job_dir), cwd=job_dir, env=env, timeout=timeout, stop=stop,
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
