"""公開リポジトリに出してはいけないものを止める道具（tools/public_check.py・tools/githooks）。

決まりの単体の試験と、使い捨てのリポジトリで本物の `git commit`・`git push` を走らせて止まること・
全体の hooks も今までどおり呼ばれることを見る。最後に、このリポジトリの追跡しているファイル全体が通ることを見る
（この機械の git の既定のアドレス・ホームのパス・名前の一覧も使う。hooks を入れていない機械でもここで引っかかる）。
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import temp_dir

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_check", ROOT / "tools" / "public_check.py")
pc = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pc   # dataclass が自分のモジュールを引くため
spec.loader.exec_module(pc)  # type: ignore[union-attr]

BAD = "taro" + "@corp-mail.co.jp"          # 試験のための架空のアドレス（ソースに続けて書かない）
GOOD = "12345+someone@users.noreply.github.com"


class RuleTests(unittest.TestCase):
    def test_addresses(self) -> None:
        for addr in (GOOD, "noreply@github.com", "noreply@anthropic.com", "kaizen@example.com", "a@b.example", "x@mail.example.org"):
            self.assertTrue(pc.allowed_address(addr), addr)
        for addr in (BAD, "someone@gmail.com", "x@users.noreply.github.com.evil.test.co"):   # public-check: allow（止まるべき例）
            self.assertFalse(pc.allowed_address(addr), addr)
        rules = pc.Rules()
        self.assertEqual(rules.find(f"連絡は {BAD} まで"), [f"メールアドレス {BAD}"])
        self.assertEqual(rules.find("@mock.patch.dict と @unittest.skipIf はアドレスではない"), [])

    def test_terms_patterns_and_allow_mark(self) -> None:
        rules = pc.Rules(terms=["ひみつ商会"], patterns=[re.compile(r"\bXY-\d+\b", re.I)])
        self.assertEqual(rules.find("ヒント: ひみつ商会の案件"), ["手元・一覧の語 ひみつ商会"])
        self.assertEqual(rules.find("課題 xy-12 を参照"), [r"一覧の型 \bXY-\d+\b"])
        self.assertEqual(rules.find(f"例の値 {BAD}  # {pc.ALLOW_MARK}"), [])

    def test_denylist_file(self) -> None:
        tmp = temp_dir(self)
        listed = tmp / "denylist.txt"
        listed.write_text("# 注釈\n\nひみつ商会\nre:\\bXY-\\d+\\b\n", encoding="utf-8")
        old = os.environ.get("QUICK_SLIDE_DENYLIST")
        os.environ["QUICK_SLIDE_DENYLIST"] = str(listed)
        self.addCleanup(lambda: os.environ.pop("QUICK_SLIDE_DENYLIST") if old is None else os.environ.__setitem__("QUICK_SLIDE_DENYLIST", old))
        terms, patterns, where = pc.load_denylist()
        self.assertEqual((terms, [p.pattern for p in patterns], where), (["ひみつ商会"], [r"\bXY-\d+\b"], str(listed)))

    def test_patch_reports_only_added_lines_with_place(self) -> None:
        patch = "\n".join([
            "diff --git a/docs/a.md b/docs/a.md", "--- a/docs/a.md", "+++ b/docs/a.md", "@@ -3,0 +4,2 @@",
            f"-{BAD} を消した", "+一行目", f"+{BAD} に聞く",
        ])
        self.assertEqual(pc.scan_patch(pc.Rules(), patch), [f"docs/a.md:5: メールアドレス {BAD}"])


class GitHookTests(unittest.TestCase):
    """使い捨てのリポジトリに tools/ を写し、全体の設定は使い捨てのファイルにして、本物の git を走らせる。"""

    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git が無い")
        tmp = temp_dir(self)
        self.repo, self.remote, self.global_hooks = tmp / "repo", tmp / "remote.git", tmp / "global-hooks"
        self.global_hooks.mkdir()
        self.called = tmp / "global-called.txt"
        for name in ("pre-commit", "pre-push"):   # 全体の hooks の代わり。呼ばれたら印を残す
            hook = self.global_hooks / name
            hook.write_text(f'#!/bin/sh\necho {name} >> "{self.called.as_posix()}"\n', encoding="utf-8")
            hook.chmod(0o755)
        gconfig = tmp / "gitconfig"
        gconfig.write_text(f"[core]\n\thooksPath = {self.global_hooks.as_posix()}\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(gconfig), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "someone", "GIT_AUTHOR_EMAIL": GOOD, "GIT_COMMITTER_NAME": "someone", "GIT_COMMITTER_EMAIL": GOOD}
        self.env.pop("QUICK_SLIDE_DENYLIST", None)
        self.git("init", "-q", "--bare", str(self.remote), cwd=tmp)
        self.git("init", "-q", str(self.repo), cwd=tmp)
        shutil.copytree(ROOT / "tools", self.repo / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        self.git("remote", "add", "origin", str(self.remote))
        out = subprocess.run([os.environ.get("PYTHON", "python3"), "tools/public_check.py", "install"], cwd=self.repo, env=self.env,
                             capture_output=True, encoding="utf-8", check=True).stdout
        self.assertIn("tools/githooks", out)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "道具を入れる")

    def git(self, *args: str, cwd: Path | None = None, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=env or self.env, capture_output=True,
                              encoding="utf-8", errors="replace", check=check)

    def test_commit_with_an_address_in_the_text_is_refused(self) -> None:
        (self.repo / "note.md").write_text(f"問い合わせ: {BAD}\n", encoding="utf-8")
        self.git("add", "note.md")
        r = self.git("commit", "-q", "-m", "メモ", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"note.md:1: メールアドレス {BAD}", r.stderr)

    def test_push_checks_author_message_and_lines_then_calls_the_global_hook(self) -> None:
        self.git("push", "-q", "origin", "main")
        pushed = self.git("rev-parse", "main").stdout.strip()
        self.assertEqual(self.called.read_text(encoding="utf-8").split(), ["pre-commit", "pre-push"])

        bad_author = {**self.env, "GIT_AUTHOR_EMAIL": BAD, "GIT_COMMITTER_EMAIL": BAD}
        (self.repo / "a.txt").write_text("ふつうの行\n", encoding="utf-8")
        self.git("add", "a.txt")
        self.git("commit", "-q", "-m", "作者のアドレスが社のもの", env=bad_author)
        r = self.git("push", "origin", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"作者のアドレスが隠しアドレスではありません: {BAD}", r.stderr)
        self.assertEqual(self.git("rev-parse", "main", cwd=self.remote).stdout.strip(), pushed)   # 相手は前のまま

        self.git("reset", "-q", "--hard", "origin/main")
        (self.repo / "b.txt").write_text("ふつうの行\n", encoding="utf-8")
        self.git("add", "b.txt")
        self.git("commit", "-q", "--no-verify", "-m", f"連絡先 {BAD}")   # コミットの検査を飛ばしても push で止まる
        r = self.git("push", "origin", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"コミットメッセージ:1: メールアドレス {BAD}", r.stderr)

        self.git("reset", "-q", "--hard", "origin/main")
        (self.repo / "c.txt").write_text(f"{BAD}\n", encoding="utf-8")
        self.git("add", "c.txt")
        self.git("commit", "-q", "--no-verify", "-m", "足した行にアドレス")
        r = self.git("push", "origin", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"c.txt:1: メールアドレス {BAD}", r.stderr)


class RepositoryTests(unittest.TestCase):
    def test_tracked_files_are_fit_to_publish(self) -> None:
        if not (ROOT / ".git").exists():
            self.skipTest("git の作業コピーではない（配った写し）")
        problems = pc.check_tree(pc.rules_here())
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
