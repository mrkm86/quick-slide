#!/usr/bin/env python3
"""リリースの手伝い（手順は docs/RELEASING.md。標準ライブラリだけ）。

    python3 tools/release.py check            # バージョンが 3 か所で揃っているか・CHANGELOG の形
    python3 tools/release.py prepare 2.1.0    # バージョンを 3 か所で上げ、CHANGELOG の「未リリース」をそのバージョンの節にする
    python3 tools/release.py notes 2.1.0      # そのバージョンの節の本文を出す（GitHub Release の本文にする）

バージョンを書く場所は `VERSION_FILES` の 3 か所だけ（製品の `__version__` とプラグインのマニフェスト 2 つ）。
タグは `v<バージョン>`。タグを打つ・リリースを作るのは人（このスクリプトは git も GitHub も触らない）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT_PY = ROOT / "src" / "quick_slide" / "__init__.py"
MANIFESTS = (ROOT / ".claude-plugin" / "plugin.json", ROOT / ".codex-plugin" / "plugin.json")
VERSION_FILES = (INIT_PY, *MANIFESTS)
CHANGELOG = ROOT / "CHANGELOG.md"

UNRELEASED = "## [Unreleased]"
_INIT_RE = re.compile(r'^__version__ = "([^"]+)"$', re.M)
_SECTION_RE = re.compile(r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?[ \t]*$", re.M)
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")


class ReleaseError(Exception):
    pass


def versions() -> dict[str, str]:
    """各ファイルに書いてあるバージョン（相対パス → バージョン）。"""
    out = {}
    m = _INIT_RE.search(INIT_PY.read_text(encoding="utf-8"))
    out[str(INIT_PY.relative_to(ROOT))] = m.group(1) if m else ""
    for path in MANIFESTS:
        out[str(path.relative_to(ROOT))] = str(json.loads(path.read_text(encoding="utf-8")).get("version", ""))
    return out


def _key(version: str) -> tuple:
    core, _, pre = version.partition("-")
    nums = tuple(int(x) for x in core.split("."))
    if not pre:
        return (*nums, 1, ())
    return (*nums, 0, tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")))


def sections(text: str) -> list[tuple[str, str | None, int, int]]:
    """CHANGELOG の節（見出しの名前・日付・本文の始まり・終わり）。"""
    heads = list(_SECTION_RE.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append((m.group(1), m.group(2), m.end(), end))
    return out


def check() -> list[str]:
    """揃っていないところ（無ければ空）。"""
    problems = []
    found = versions()
    if len(set(found.values())) != 1:
        problems.append("バージョンが揃っていません: " + ", ".join(f"{k}={v or '(無し)'}" for k, v in found.items()))
    current = next(iter(found.values()))
    if not _SEMVER.match(current):
        problems.append(f"バージョンが SemVer の形ではありません: {current!r}")
    text = CHANGELOG.read_text(encoding="utf-8")
    secs = sections(text)
    if not secs or secs[0][0] != "Unreleased":
        problems.append(f"CHANGELOG.md の最初の節は {UNRELEASED} にします")
    released = [(name, date) for name, date, _s, _e in secs if name != "Unreleased"]
    for name, date in released:
        if not _SEMVER.match(name) or date is None:
            problems.append(f"CHANGELOG.md の見出しの形が違います: ## [{name}]（## [X.Y.Z] - YYYY-MM-DD）")
    good = [name for name, _d in released if _SEMVER.match(name)]
    if good != sorted(good, key=_key, reverse=True) or len(set(good)) != len(good):
        problems.append("CHANGELOG.md のバージョンは新しい順に 1 回ずつ並べます")
    if good and _SEMVER.match(current) and _key(good[0]) > _key(current):
        problems.append(f"CHANGELOG.md に今のバージョン（{current}）より新しいバージョン（{good[0]}）があります")
    return problems


def notes(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    for name, _date, start, end in sections(text):
        if name == version:
            body = text[start:end].strip()
            if not body:
                raise ReleaseError(f"CHANGELOG.md の {version} の節が空です")
            return body + "\n"
    raise ReleaseError(f"CHANGELOG.md に {version} の節がありません")


def prepare(version: str, *, today: _dt.date | None = None) -> None:
    if not _SEMVER.match(version):
        raise ReleaseError(f"SemVer の形で書いてください（例: 2.1.0）: {version!r}")
    problems = check()
    if problems:
        raise ReleaseError("先に直してください:\n- " + "\n- ".join(problems))
    current = next(iter(versions().values()))
    text = CHANGELOG.read_text(encoding="utf-8")
    secs = sections(text)
    released = [name for name, *_ in secs if name != "Unreleased"]
    if version in released:
        raise ReleaseError(f"{version} はもう CHANGELOG.md にあります")
    # まだ一度も出していないバージョン（最初のリリース）なら、今のバージョンのまま出せる
    if _key(version) < _key(current) or (_key(version) == _key(current) and current in released):
        raise ReleaseError(f"今のバージョン（{current}）より新しいバージョンにしてください")
    _name, _date, start, end = secs[0]
    if not text[start:end].strip():
        raise ReleaseError("CHANGELOG.md の「未リリース」が空です。何が変わったかを先に書いてください")
    date = (today or _dt.date.today()).isoformat()
    # 「未リリース」の見出しはそのまま残し、その直後にバージョンの見出しを差す（中身はバージョンの節になる）
    text = text[:start] + f"\n\n## [{version}] - {date}" + text[start:]
    CHANGELOG.write_text(text, encoding="utf-8")
    init = INIT_PY.read_text(encoding="utf-8")
    INIT_PY.write_text(_INIT_RE.sub(f'__version__ = "{version}"', init, count=1), encoding="utf-8")
    for path in MANIFESTS:
        raw = path.read_text(encoding="utf-8")
        path.write_text(re.sub(r'("version":\s*")[^"]*(")', rf"\g<1>{version}\g<2>", raw, count=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    for name in ("prepare", "notes"):
        sub.add_parser(name).add_argument("version")
    args = p.parse_args(argv)
    try:
        if args.cmd == "check":
            problems = check()
            for problem in problems:
                print(problem, file=sys.stderr)
            if not problems:
                print(next(iter(versions().values())))
            return 1 if problems else 0
        if args.cmd == "notes":
            sys.stdout.write(notes(args.version))
            return 0
        prepare(args.version)
        print(f"{args.version} にしました。CHANGELOG.md を見直してからコミットし、docs/RELEASING.md の続きへ")
        return 0
    except ReleaseError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
