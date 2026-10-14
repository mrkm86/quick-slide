#!/usr/bin/env python3
"""会話から Quick Slide を新しいバージョンにする（「Quick Slide を更新して」。docs/adr/0020-release-and-update-notice.md）。

    python3 quick_slide_update.py          # 確かめるだけ（何も変えない）: いまの版・最新のバージョン・変更点・何をするか
    python3 quick_slide_update.py --yes    # 更新する（会話で利用者に聞いてから）

やること:

1. 最新のバージョンを GitHub に確かめる（製品の `python -m quick_slide.updates` を子プロセスで呼ぶ。設定の切り替えと
   環境変数 `QUICK_SLIDE_NO_UPDATE_CHECK` は自動の確かめだけを止めるもので、頼まれた確かめは止めない）
2. このプラグインが、どのアプリのプラグインの置き場から読まれているかを見る（Claude Code・Codex・それ以外のフォルダ）
3. `--yes` なら、そのアプリのコマンドで更新する（Claude Code: `claude plugin marketplace update` → `claude plugin update`、
   Codex: `codex plugin marketplace upgrade` → `codex plugin add`）。終わったら、置き場に新しいバージョンが入ったかを確かめる
4. 動いているサーバには触らない。次に Quick Slide を使ったとき、新しいバージョンの入口が古いバージョンのサーバを起こし直す
   （`quick_slide_start.py` の `ensure_server`。処理中の作業があれば待つ）

フォルダから読んでいる（開発中の写しなど）ときは、何も流さずに `manual` を返す（git を勝手に動かさない）。

出力: **stdout の最終行は JSON 1 行**。`status` は up_to_date / update_available / updated / manual / error。
終了コードは 0=成功（確かめた・更新した・手でしてもらう）/ 4=更新のコマンドが失敗した・確かめられなかった。
標準ライブラリだけを使う（サーバ本体を import しない）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clidirs  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 4
PLUGIN = "quick-slide"
COMMAND_TIMEOUT = 300.0
CHECK_TIMEOUT = 30.0
# Codex が会話の中でコマンドを走らせるときに付く印（quick_slide_start.py と同じ）
CODEX_MARKERS = ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_THREAD_ID", "CODEX_SESSION_ID")
_VERSION_RE = re.compile(r'^__version__ = "([^"]+)"$', re.M)

AFTER = {
    "claude": "Claude Code で /reload-plugins を実行するか、新しい会話を始めると、新しいバージョンが使えます。",
    "codex": "Codex を起動し直すと、新しいバージョンが使えます。サーバをターミナルで起こしているときは、"
             "ターミナルで --start-server をもう一度流してください（古いバージョンのサーバは、新しいバージョンに入れ替わります）。",
}
SERVER_NOTE = "動いている Quick Slide のサーバは、次に Quick Slide を使ったときに新しいバージョンで起動し直します（処理中の作業があれば、終わってから）。"


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def plugin_root() -> Path:
    """`skills/quick-slide/scripts/` から 3 つ上（プラグインの根）。"""
    return Path(__file__).resolve().parents[3]


def version_of(root: Path) -> str | None:
    try:
        m = _VERSION_RE.search((root / "src" / "quick_slide" / "__init__.py").read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def _app_dirs(env: dict[str, str]) -> dict[str, Path]:
    home = Path.home()
    return {"claude": Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser(),
            "codex": Path(env.get("CODEX_HOME") or home / ".codex").expanduser()}


def install_of(root: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    """どこから読まれているか。アプリの置き場は `<アプリ>/plugins/cache/<マーケットプレイス>/<プラグイン>/<版>/`。"""
    env = dict(os.environ if env is None else env)
    parts = root.parents
    if len(parts) >= 5 and parts[2].name == "cache" and parts[3].name == "plugins":
        app_dir = parts[4]
        kind = None
        for name, d in _app_dirs(env).items():
            try:
                if d.resolve() == app_dir.resolve():
                    kind = name
            except OSError:
                pass
        if kind is None:   # 置き場を環境変数で替えているのに、ここでは見えていないとき
            kind = "codex" if "codex" in app_dir.name.lower() else "claude" if "claude" in app_dir.name.lower() else None
        if kind is not None:
            return {"kind": kind, "marketplace": parts[1].name, "plugin": parts[0].name, "root": str(root)}
    return {"kind": "folder", "root": str(root), "git": (root / ".git").exists()}


def commands_for(install: dict[str, Any]) -> list[list[str]]:
    mkt, plugin = install.get("marketplace"), install.get("plugin") or PLUGIN
    if install["kind"] == "claude":
        return [["claude", "plugin", "marketplace", "update", mkt], ["claude", "plugin", "update", f"{plugin}@{mkt}"]]
    if install["kind"] == "codex":
        return [["codex", "plugin", "marketplace", "upgrade", mkt], ["codex", "plugin", "add", f"{plugin}@{mkt}"]]
    return []


def check_latest(root: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run([sys.executable, "-m", "quick_slide.updates"], env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=CHECK_TIMEOUT, stdin=subprocess.DEVNULL)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        got = json.loads(lines[-1]) if proc.returncode == 0 and lines else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        got = None
    return got if isinstance(got, dict) else {"error": "unreachable"}


def installed_version(install: dict[str, Any], version: str) -> str | None:
    """更新のあと、アプリの置き場にそのバージョンが入ったか（`<…>/<プラグイン>/<版>/src/quick_slide/__init__.py`）。"""
    candidate = Path(install["root"]).parent / version
    return version_of(candidate) if candidate.is_dir() else None


def run_commands(commands: list[list[str]]) -> tuple[bool, list[dict[str, Any]]]:
    done = []
    env = clidirs.with_search_path(os.environ)
    for cmd in commands:
        exe = clidirs.which(cmd[0], env)
        if exe is None:
            done.append({"command": " ".join(cmd), "ok": False, "reason": "not_found"})
            return False, done
        try:
            proc = subprocess.run([exe, *cmd[1:]], env=env, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=COMMAND_TIMEOUT, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            done.append({"command": " ".join(cmd), "ok": False, "reason": exc.__class__.__name__})
            return False, done
        tail = (proc.stdout + proc.stderr).strip()[-800:]
        done.append({"command": " ".join(cmd), "ok": proc.returncode == 0, "exit_code": proc.returncode, "output": tail})
        if proc.returncode != 0:
            return False, done
    return True, done


def run(args: argparse.Namespace) -> int:
    root = plugin_root()
    current = version_of(root)
    latest = check_latest(root)
    install = install_of(root)
    commands = commands_for(install)
    out: dict[str, Any] = {
        "current_version": current, "latest_version": latest.get("latest_version"),
        "release_url": latest.get("release_url"), "notes": latest.get("notes"),
        "install": install, "commands": [" ".join(c) for c in commands],
        "inside_codex": any(os.environ.get(n) for n in CODEX_MARKERS),
    }
    if latest.get("error") and not latest.get("latest_version"):
        out.update(status="error", code=latest["error"],
                   message="新しいバージョンを確かめられませんでした（GitHub につながらないか、公開されたリリースがまだありません）。")
        emit(out)
        return EXIT_FAILED
    if not latest.get("update_available"):
        out.update(status="up_to_date", message=f"現在のバージョン（{current}）が最新です。")
        emit(out)
        return EXIT_OK
    if install["kind"] == "folder":
        how = f"git -C {root} pull" if install.get("git") else "そのフォルダを新しいバージョンに置き換える"
        out.update(status="manual", message=f"新しいバージョン {out['latest_version']} が公開されています。この Quick Slide はアプリのプラグインの置き場ではなく、"
                   f"フォルダ（{root}）から読まれているので、ここからは更新しません。フォルダを更新してください（{how}）。")
        emit(out)
        return EXIT_OK
    if not args.yes:
        out.update(status="update_available",
                   message=f"新しいバージョン {out['latest_version']} が公開されています（現在：{current}）。更新してよいか、利用者に聞いてから --yes で流します。")
        emit(out)
        return EXIT_OK
    ok, done = run_commands(commands)
    out["ran"] = done
    if not ok:
        missing = done and done[-1].get("reason") == "not_found"
        out.update(status="manual" if missing else "error", code="cli_not_found" if missing else "update_failed",
                   message=("更新のコマンドが見つかりませんでした。" if missing else "更新のコマンドが失敗しました。")
                   + "利用者のターミナルで次を流してもらってください: " + " && ".join(out["commands"]))
        emit(out)
        return EXIT_OK if missing else EXIT_FAILED
    got = installed_version(install, out["latest_version"])
    out["installed_version"] = got
    if got != out["latest_version"]:
        out.update(status="error", code="not_installed",
                   message=f"更新のコマンドは通りましたが、{out['latest_version']} が入ったことを確かめられませんでした。"
                           "アプリを起動し直してから、もう一度「Quick Slide を更新して」と頼んでください。")
        emit(out)
        return EXIT_FAILED
    out.update(status="updated", message=f"Quick Slide を {current} から {got} にしました。{AFTER[install['kind']]}{SERVER_NOTE}")
    emit(out)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="quick_slide_update.py", description="会話から Quick Slide を新しいバージョンにする")
    p.add_argument("--yes", action="store_true", help="更新する（会話で利用者に聞いてから使う。付けなければ確かめるだけ）")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):
        _out.reconfigure(encoding="utf-8", newline="\n")
    raise SystemExit(main())
