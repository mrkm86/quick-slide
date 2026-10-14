"""CLI の探し方（clidirs.py）。PATH で見つからなければ、よくある置き場も見る。

Codex のサンドボックスの中から起こされたサーバは、利用者のシェルの PATH を受け継がないことがある。そのときでも
台本係（claude・codex）とエンジンの子（codex・node）が見つかるか。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from helpers import REPO, temp_dir

from quick_slide import clidirs
from quick_slide.childenv import child_env
from quick_slide.worker.tools import Tools

COPIES = [REPO / "src" / "quick_slide" / "clidirs.py", REPO / "skills" / "quick-slide" / "scripts" / "clidirs.py"]


def put_command(folder: Path, name: str) -> Path:
    """`folder` に、コマンドとして見つかる空の実行ファイルを置く（Windows は `.cmd`）。"""
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        path = folder / f"{name}.cmd"
        path.write_text("@exit /b 0\r\n", encoding="ascii", newline="")
    else:
        path = folder / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        path.chmod(0o755)
    return path


class CopiesTest(unittest.TestCase):
    def test_the_two_copies_are_the_same(self) -> None:
        first = COPIES[0].read_bytes()
        for path in COPIES[1:]:
            self.assertEqual(path.read_bytes(), first, f"{path.relative_to(REPO).as_posix()} がサーバの写しとずれた")


class SearchPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self, "qs-clidirs-")

    def test_the_override_replaces_the_common_places_and_empty_means_none(self) -> None:
        a, b = self.tmp / "a", self.tmp / "b"
        self.assertEqual(clidirs.common_dirs({clidirs.ENV_DIRS: os.pathsep.join([str(a), str(b)])}), [str(a), str(b)])
        self.assertEqual(clidirs.common_dirs({clidirs.ENV_DIRS: ""}), [])

    def test_common_places_go_after_the_users_path_without_duplicates(self) -> None:
        mine, extra = self.tmp / "mine", self.tmp / "extra"
        env = {"PATH": str(mine), clidirs.ENV_DIRS: os.pathsep.join([str(mine), str(extra)])}
        self.assertEqual(clidirs.search_path(env["PATH"], env), os.pathsep.join([str(mine), str(extra)]))
        self.assertEqual(clidirs.with_search_path(env)["PATH"], os.pathsep.join([str(mine), str(extra)]))
        # 足すものが無ければ PATH に触らない（無い PATH を空の PATH にしない）
        self.assertNotIn("PATH", clidirs.with_search_path({clidirs.ENV_DIRS: ""}))

    def test_known_places_under_home_are_found_only_when_they_exist(self) -> None:
        home = self.tmp / "home"
        prefix = self.tmp / "npm-prefix"
        npm_bin = prefix if os.name == "nt" else prefix / "bin"
        npm_bin.mkdir(parents=True)
        home.mkdir()
        (home / ".npmrc").write_text(f"registry=https://example.invalid/\nprefix={prefix}\n", encoding="utf-8")
        local = home / ".local" / "bin"
        local.mkdir(parents=True)
        env = {"HOME": str(home), "USERPROFILE": str(home)}
        if os.name == "nt":
            appdata = self.tmp / "appdata"
            (appdata / "npm").mkdir(parents=True)
            env["APPDATA"] = str(appdata)
        found = clidirs.common_dirs(env)
        self.assertIn(str(npm_bin), found, "npm の prefix（.npmrc）の置き場")
        self.assertIn(str(local), found)
        if os.name == "nt":
            self.assertIn(str(appdata / "npm"), found, "npm のグローバルの既定（%APPDATA%\\npm）")
        else:
            self.assertNotIn(str(home / ".npm-global" / "bin"), found, "無い置き場は足さない")

    @unittest.skipIf(os.name == "nt", "nvm の versions の置き場は mac・Linux")
    def test_the_newest_nvm_node_comes_first(self) -> None:
        home = self.tmp / "home"
        for v in ("v9.0.0", "v20.11.1", "v18.19.0"):
            (home / ".nvm" / "versions" / "node" / v / "bin").mkdir(parents=True)
        found = [d for d in clidirs.common_dirs({"HOME": str(home)}) if ".nvm" in d]
        self.assertEqual([Path(d).parent.name for d in found], ["v20.11.1", "v18.19.0", "v9.0.0"])

    def test_which_looks_at_the_common_places_after_path(self) -> None:
        extra = self.tmp / "npm-global"
        put_command(extra, "claude")
        env = {"PATH": str(self.tmp / "empty"), clidirs.ENV_DIRS: str(extra)}
        found = clidirs.which("claude", env)
        self.assertIsNotNone(found)
        self.assertEqual(Path(found).parent, extra)
        self.assertIsNone(clidirs.which("claude", {**env, clidirs.ENV_DIRS: ""}))


class ServerSideTest(unittest.TestCase):
    """サーバの側: 台本係の CLI を見つけ、子の PATH にもその置き場が入る。"""

    def setUp(self) -> None:
        self.tmp = temp_dir(self, "qs-clidirs-")
        self.extra = self.tmp / "npm-global"
        put_command(self.extra, "claude")
        put_command(self.extra, "codex")

    def test_the_writer_cli_is_found_outside_path(self) -> None:
        env = {"PATH": str(self.tmp / "empty"), clidirs.ENV_DIRS: str(self.extra)}
        with mock.patch.dict(os.environ, env):
            for name in ("QUICK_SLIDE_CLAUDE", "QUICK_SLIDE_CODEX_TEXT"):
                os.environ.pop(name, None)
            tools = Tools()
            self.assertEqual(Path(tools.claude_path()).parent, self.extra)
            self.assertEqual(Path(tools.codex_path()).parent, self.extra)

    def test_children_get_the_common_places_on_path(self) -> None:
        env = child_env(home=self.tmp / "qs", base={"PATH": "/usr/bin", clidirs.ENV_DIRS: str(self.extra)})
        self.assertEqual(env["PATH"], os.pathsep.join(["/usr/bin", str(self.extra)]))


if __name__ == "__main__":
    unittest.main()
