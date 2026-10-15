#!/usr/bin/env python3
"""公開リポジトリに出してはいけないものを、push の前に止める（標準ライブラリだけ）。

    python3 tools/public_check.py install     # このリポジトリの hooks を tools/githooks にする（全体の hooks も今までどおり呼ぶ）
    python3 tools/public_check.py tree        # 追跡しているファイル全体を見る
    python3 tools/public_check.py staged      # コミットしようとしている変更を見る（pre-commit）
    python3 tools/public_check.py pre-push <remote>   # 標準入力に pre-push の行（push しようとしているコミットを見る）

見るもの:
- メールアドレス。GitHub の隠しアドレスと例示用のもの（example.com など）のほかは全部止める
- 手元の機械のもの。git の既定のアドレス・ホームのパス・機械の名前は、その場で読む
- 名前の一覧。社内の名前などは一覧そのものを公開できないので、リポジトリの外に置き、
  `git config quick-slide.denylist <ファイル>` で場所を教える（1 行 1 語・大文字小文字は区別しない・`re:` で始まる行は正規表現・`#` は注釈）
- push では、コミットの作者とコミッタのアドレス・コミットメッセージ・足した行

わざと書く行（試験の例など）は、同じ行に `public-check: allow` と書けば通す。
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = "tools/githooks"
ALLOW_MARK = "public-check: allow"
ZERO = re.compile(r"^0+$")
EMAIL = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# 出してよいアドレス（GitHub の隠しアドレス・自動のアドレス・例示用）
ALLOWED_ADDRESSES = {"noreply@github.com", "noreply@anthropic.com"}
ALLOWED_DOMAINS = ("users.noreply.github.com", "example.com", "example.org", "example.net")
ALLOWED_TLDS = (".example", ".test", ".invalid", ".localhost")


def allowed_address(addr: str) -> bool:
    addr = addr.lower()
    domain = addr.rpartition("@")[2]
    return (addr in ALLOWED_ADDRESSES or domain in ALLOWED_DOMAINS
            or any(domain.endswith("." + d) for d in ALLOWED_DOMAINS) or domain.endswith(ALLOWED_TLDS))


@dataclass
class Rules:
    """止める語。`terms` は部分一致（大文字小文字を区別しない）、`patterns` は正規表現。"""
    terms: list[str] = field(default_factory=list)
    patterns: list[re.Pattern[str]] = field(default_factory=list)

    def find(self, line: str) -> list[str]:
        if ALLOW_MARK in line:
            return []
        found = [f"メールアドレス {m.group(0)}" for m in EMAIL.finditer(line) if not allowed_address(m.group(0))]
        low = line.lower()
        found += [f"手元・一覧の語 {t}" for t in self.terms if t.lower() in low]
        found += [f"一覧の型 {p.pattern}" for p in self.patterns if p.search(line)]
        return found


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} が失敗しました: {r.stderr.strip()}")
    return r.stdout


def local_terms(cwd: Path = ROOT) -> list[str]:
    """この機械の持ち物で、公開してはいけないもの（git の既定のアドレス・ホームのパス・機械の名前）。"""
    out = []
    for scope in ("--global", "--system"):
        addr = git("config", scope, "--get", "user.email", cwd=cwd, check=False).strip()
        if addr and not allowed_address(addr):
            out.append(addr)
    home = str(Path.home())
    if len(Path(home).parts) > 2:   # `/root` や `C:\` のような短いものは、ふつうの文にも出るので見ない
        out += [home, home.replace("\\", "/")]
    host = socket.gethostname().split(".")[0]
    if len(host) >= 5 and host.lower() not in {"localhost", "runner"}:
        out.append(host)
    return sorted(set(out), key=out.index)


def load_denylist(cwd: Path = ROOT) -> tuple[list[str], list[re.Pattern[str]], str | None]:
    where = os.environ.get("QUICK_SLIDE_DENYLIST") or git("config", "--get", "quick-slide.denylist", cwd=cwd, check=False).strip()
    if not where:
        return [], [], None
    path = Path(where).expanduser()
    if not path.is_file():
        raise SystemExit(f"名前の一覧が見つかりません: {path}（git config quick-slide.denylist を直してください）")
    terms, patterns = [], []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            patterns.append(re.compile(line[3:].strip(), re.I))
        else:
            terms.append(line)
    return terms, patterns, str(path)


def rules_here(cwd: Path = ROOT) -> Rules:
    terms, patterns, _ = load_denylist(cwd)
    return Rules(terms=local_terms(cwd) + terms, patterns=patterns)


def scan_text(rules: Rules, where: str, text: str, first_line: int = 1) -> list[str]:
    out = []
    for i, line in enumerate(text.splitlines(), first_line):
        out += [f"{where}:{i}: {f}" for f in rules.find(line)]
    return out


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def check_tree(rules: Rules, cwd: Path = ROOT) -> list[str]:
    out = []
    for rel in git("ls-files", "-z", cwd=cwd).split("\0"):
        path = cwd / rel
        if not rel or not path.is_file():
            continue
        data = path.read_bytes()
        if not is_binary(data):
            out += scan_text(rules, rel, data.decode("utf-8", errors="replace"))
        out += [f"{rel}: ファイルの名前に {f}" for f in rules.find(rel)]
    return out


def scan_patch(rules: Rules, patch: str, label: str = "") -> list[str]:
    """`git diff -U0` の足した行だけを見る。"""
    out, path, line_no = [], "", 0
    for line in patch.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
            out += [f"{label}{path}: ファイルの名前に {f}" for f in rules.find(path)]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            line_no = int(m.group(1)) if m else 0
        elif line.startswith("+"):
            out += [f"{label}{path}:{line_no}: {f}" for f in rules.find(line[1:])]
            line_no += 1
    return out


def check_staged(rules: Rules, cwd: Path = ROOT) -> list[str]:
    return scan_patch(rules, git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff", cwd=cwd))


def check_commits(rules: Rules, shas: list[str], cwd: Path = ROOT) -> list[str]:
    out = []
    for sha in shas:
        head = git("log", "-1", "--format=%ae%x00%ce%x00%an%x00%cn%x00%B", sha, cwd=cwd)
        author, committer, aname, cname, message = head.split("\0", 4)
        label = f"{sha[:7]} "
        for who, addr in (("作者", author), ("コミッタ", committer)):
            if not allowed_address(addr):
                out.append(f"{label}{who}のアドレスが隠しアドレスではありません: {addr}")
        for who, name in (("作者", aname), ("コミッタ", cname)):
            out += [f"{label}{who}の名前に {f}" for f in rules.find(name)]
        out += scan_text(rules, f"{label}コミットメッセージ", message)
        if len(git("rev-list", "--parents", "-n", "1", sha, cwd=cwd).split()) <= 2:   # マージは両側のコミットで見る
            out += scan_patch(rules, git("show", "--format=", "-U0", "--no-color", "--no-ext-diff", sha, cwd=cwd), label)
    return out


def pushed_commits(lines: list[str], remote: str, cwd: Path = ROOT) -> list[str]:
    """pre-push の行から、まだ相手に無いコミットを集める（消す push は見ない）。"""
    shas: list[str] = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4 or ZERO.match(parts[1]):
            continue
        for sha in git("rev-list", parts[1], "--not", f"--remotes={remote}", cwd=cwd).split():
            if sha not in shas:
                shas.append(sha)
    return shas


def install(cwd: Path = ROOT) -> list[str]:
    git("config", "core.hooksPath", HOOKS_DIR, cwd=cwd)
    notes = [f"このリポジトリの hooks を {HOOKS_DIR} にしました（全体の hooks も今までどおり呼びます）"]
    addr = git("config", "--get", "user.email", cwd=cwd, check=False).strip()
    if not allowed_address(addr):
        notes.append(f"注意: コミットのアドレスが隠しアドレスではありません（{addr or '未設定'}）。"
                     "git config user.email <id>+<名前>@users.noreply.github.com にしてください")
    _, _, where = load_denylist(cwd)
    notes.append(f"名前の一覧: {where}" if where else
                 "名前の一覧はまだありません。リポジトリの外に置き、git config quick-slide.denylist <ファイル> で教えてください")
    return notes


def report(problems: list[str], what: str) -> int:
    if not problems:
        return 0
    print(f"公開リポジトリに出してはいけないものが{what}にあります:", file=sys.stderr)
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    print(f"直してからやり直してください。わざと書く行には `{ALLOW_MARK}` を添えます。", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")   # Windows の端末でも日本語を出す
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("install", "tree", "staged"):
        sub.add_parser(name)
    sub.add_parser("pre-push").add_argument("remote")
    args = p.parse_args(argv)
    if args.cmd == "install":
        print("\n".join(install()))
        return 0
    rules = rules_here()
    if args.cmd == "tree":
        return report(check_tree(rules), "追跡しているファイル")
    if args.cmd == "staged":
        return report(check_staged(rules), "コミットしようとしている変更")
    return report(check_commits(rules, pushed_commits(sys.stdin.read().splitlines(), args.remote)), "push しようとしているコミット")


if __name__ == "__main__":
    sys.exit(main())
