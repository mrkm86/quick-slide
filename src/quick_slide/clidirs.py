"""`claude`・`codex`・`node` を探す 1 か所。PATH で見つからなければ、よくある置き場も見る。

Codex のサンドボックスの中や、PATH を引き継がないアプリから起こされたときは、PATH が利用者のシェルと違い、
npm のグローバルの置き場などが抜けていることがある。そこから起こしたサーバとその子（台本係・エンジン）が
CLI を見失わないよう、よくある置き場のうち在るものを PATH の**後ろ**に足す（利用者の PATH にあるものが先に効く）。
`codex`・`claude` が npm の包み（`#!/usr/bin/env node`・`codex.cmd`）でも、同じ置き場の `node` で動く。

**同じ中身のファイルを 2 か所に置く**: `src/quick_slide/clidirs.py`（サーバ）と `skills/quick-slide/scripts/clidirs.py`
（導入の確かめ。サーバを import しない）。片方を直したら両方に写す（試験がバイト単位で見比べる）。標準ライブラリだけで書く。

環境変数 `QUICK_SLIDE_CLI_DIRS`（パスの区切りで並べる）があれば、よくある置き場の代わりにそれを見る。空なら何も足さない
（試験が本物の CLI を見ないようにする口でもある）。
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path

ENV_DIRS = "QUICK_SLIDE_CLI_DIRS"
WINDOWS = os.name == "nt"


def _npm_prefix_bins(env: Mapping[str, str], home: Path | None) -> list[Path]:
    """npm のグローバルの置き場を利用者が変えていれば、その実行ファイルの置き場（`npm config set prefix`）。"""
    prefixes = [env.get("NPM_CONFIG_PREFIX") or env.get("npm_config_prefix")]
    if home is not None:
        try:
            for line in (home / ".npmrc").read_text(encoding="utf-8").splitlines():
                m = re.match(r"\s*prefix\s*=\s*(.+?)\s*$", line)
                if m:
                    prefixes.append(m.group(1).strip("\"'"))
        except (OSError, UnicodeDecodeError):
            pass
    out = []
    for p in prefixes:
        if p:
            # .npmrc は `${名前}` で環境変数を引ける
            prefix = Path(re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), ""), p)).expanduser()
            out.append(prefix if WINDOWS else prefix / "bin")
    return out


def _version_key(path: Path) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", path.parent.name)[:3])


def common_dirs(env: Mapping[str, str] | None = None) -> list[str]:
    """よくある置き場のうち、在るもの（`QUICK_SLIDE_CLI_DIRS` があればそれ）。"""
    env = os.environ if env is None else env
    if ENV_DIRS in env:
        return [d for d in env[ENV_DIRS].split(os.pathsep) if d]
    home_value = env.get("USERPROFILE" if WINDOWS else "HOME")
    home = Path(home_value) if home_value else None
    candidates: list[Path] = _npm_prefix_bins(env, home)
    if WINDOWS:
        for name, sub in (("APPDATA", "npm"), ("NVM_SYMLINK", ""), ("ProgramFiles", "nodejs"),
                          ("LOCALAPPDATA", r"Volta\bin")):
            if env.get(name):
                candidates.append(Path(env[name]) / sub)
        candidates.append(Path(r"C:\nvm4w\nodejs"))   # nvm for Windows の既定の置き場（NVM_SYMLINK が見えないとき）
        if home is not None:
            candidates.append(home / ".local" / "bin")   # Claude Code の単体の導入
    else:
        if home is not None:
            candidates += [home / ".local" / "bin", home / ".claude" / "local", home / ".npm-global" / "bin",
                           home / ".volta" / "bin"]
        if env.get("NVM_BIN"):
            candidates.append(Path(env["NVM_BIN"]))
        if home is not None:
            try:
                candidates += sorted((home / ".nvm" / "versions" / "node").glob("*/bin"), key=_version_key, reverse=True)
            except OSError:
                pass
        candidates += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    out: list[str] = []
    for c in candidates:
        if str(c) not in out and c.is_dir():
            out.append(str(c))
    return out


def search_path(path: str | None, env: Mapping[str, str] | None = None) -> str:
    """`path`（PATH の値）の後ろに、よくある置き場のうちまだ入っていないものを足す。"""
    parts = [p for p in (path or "").split(os.pathsep) if p]
    seen = {os.path.normcase(os.path.normpath(p)) for p in parts}
    for d in common_dirs(env):
        key = os.path.normcase(os.path.normpath(d))
        if key not in seen:
            seen.add(key)
            parts.append(d)
    return os.pathsep.join(parts)


def with_search_path(env: Mapping[str, str]) -> dict[str, str]:
    """`env` の PATH に、よくある置き場を足した写し（子プロセスに渡す環境）。足すものが無ければ PATH は触らない。"""
    out = dict(env)
    value = search_path(out.get("PATH"), out)
    if value != (out.get("PATH") or ""):
        out["PATH"] = value
    return out


def which(command: str, env: Mapping[str, str] | None = None) -> str | None:
    """`shutil.which` と同じ。ただし PATH の後ろに、よくある置き場も見る。"""
    env = os.environ if env is None else env
    return shutil.which(command, path=search_path(env.get("PATH"), env))
