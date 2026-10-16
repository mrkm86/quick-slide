"""会話からの更新（`skills/quick-slide/scripts/quick_slide_update.py`）と、古い版のサーバの入れ替えの断り方
（`quick_slide_start.replace_outdated_server`）。GitHub もアプリの CLI も呼ばない（差し替える）。
入れ替えを本物のサーバで確かめるのは tests/test_skill_start.py。"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import REPO, temp_dir

SCRIPTS = REPO / "skills" / "quick-slide" / "scripts"


def load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"skill_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


update = load("quick_slide_update")
start = load("quick_slide_start")


def plugin_at(root: Path, version: str) -> Path:
    (root / "src" / "quick_slide").mkdir(parents=True, exist_ok=True)
    (root / "src" / "quick_slide" / "__init__.py").write_text(f'__all__ = ["__version__"]\n__version__ = "{version}"\n', encoding="utf-8")
    return root


def latest(version: str, current: str = "2.0.0") -> dict:
    return {"current_version": current, "latest_version": version, "update_available": start.is_newer(version, current),
            "release_url": f"https://github.com/x/y/releases/tag/v{version}", "notes": "- 新しいこと", "error": None}


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self)

    def test_claude_and_codex_caches(self) -> None:
        env = {"CLAUDE_CONFIG_DIR": str(self.tmp / ".claude"), "CODEX_HOME": str(self.tmp / ".codex")}
        root = plugin_at(self.tmp / ".claude/plugins/cache/my-market/quick-slide/2.0.0", "2.0.0")
        got = update.install_of(root, env)
        self.assertEqual((got["kind"], got["marketplace"], got["plugin"]), ("claude", "my-market", "quick-slide"))
        self.assertEqual(update.commands_for(got), [["claude", "plugin", "marketplace", "update", "my-market"],
                                                    ["claude", "plugin", "update", "quick-slide@my-market"]])
        root = plugin_at(self.tmp / ".codex/plugins/cache/quick-slide/quick-slide/2.0.0", "2.0.0")
        got = update.install_of(root, env)
        self.assertEqual(got["kind"], "codex")
        self.assertEqual(update.commands_for(got), [["codex", "plugin", "marketplace", "upgrade", "quick-slide"],
                                                    ["codex", "plugin", "add", "quick-slide@quick-slide"]])

    def test_a_folder_is_not_touched(self) -> None:
        got = update.install_of(REPO, {})
        self.assertEqual(got["kind"], "folder")
        self.assertEqual(update.commands_for(got), [])


class UpdateRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self)
        self.cache = self.tmp / ".claude/plugins/cache/quick-slide/quick-slide"
        self.root = plugin_at(self.cache / "2.0.0", "2.0.0")
        # ホームも試験のフォルダにする（実の ~/.codex を見ない。Windows は USERPROFILE が無いとホームを決められない）
        env = {"CLAUDE_CONFIG_DIR": str(self.tmp / ".claude"), "PATH": "", "HOME": str(self.tmp), "USERPROFILE": str(self.tmp)}
        for target, value in (("plugin_root", lambda: self.root), ("os.environ", env)):
            p = mock.patch.object(update, target, value) if target != "os.environ" else mock.patch.dict(update.os.environ, value, clear=True)
            p.start()
            self.addCleanup(p.stop)

    def run_script(self, *argv: str, answer: dict, commands_ok: bool = True, installs: str | None = None) -> tuple[int, dict]:
        ran: list = []

        def fake_run(commands):
            ran.extend(commands)
            if installs:
                plugin_at(self.cache / installs, installs)
            return commands_ok, [{"command": " ".join(c), "ok": commands_ok} for c in commands]

        out: list = []
        with mock.patch.object(update, "check_latest", return_value=answer), \
                mock.patch.object(update, "run_commands", side_effect=fake_run), \
                mock.patch.object(update, "emit", side_effect=out.append):
            rc = update.main(list(argv))
        self.ran = ran
        return rc, out[-1]

    def test_check_only_changes_nothing(self) -> None:
        rc, out = self.run_script(answer=latest("2.1.0"))
        self.assertEqual((rc, out["status"], out["latest_version"]), (0, "update_available", "2.1.0"))
        self.assertEqual(self.ran, [])
        self.assertIn("claude plugin update quick-slide@quick-slide", out["commands"])

    def test_up_to_date(self) -> None:
        rc, out = self.run_script("--yes", answer=latest("2.0.0"))
        self.assertEqual((rc, out["status"]), (0, "up_to_date"))
        self.assertEqual(self.ran, [])

    def test_update_and_verify(self) -> None:
        rc, out = self.run_script("--yes", answer=latest("2.1.0"), installs="2.1.0")
        self.assertEqual((rc, out["status"], out["installed_version"]), (0, "updated", "2.1.0"))
        self.assertEqual(len(self.ran), 2)
        self.assertIn("/reload-plugins", out["message"])

    def test_commands_passed_but_the_new_version_is_not_there(self) -> None:
        rc, out = self.run_script("--yes", answer=latest("2.1.0"))
        self.assertEqual((rc, out["status"], out["code"]), (4, "error", "not_installed"))

    def test_failed_commands_hand_over_to_the_terminal(self) -> None:
        rc, out = self.run_script("--yes", answer=latest("2.1.0"), commands_ok=False)
        self.assertEqual((rc, out["code"]), (4, "update_failed"))
        self.assertIn("claude plugin marketplace update quick-slide", out["message"])

    def test_could_not_check(self) -> None:
        rc, out = self.run_script("--yes", answer={"error": "unreachable"})
        self.assertEqual((rc, out["status"]), (4, "error"))
        self.assertEqual(self.ran, [])

    def test_folder_install_is_manual(self) -> None:
        self.root = plugin_at(self.tmp / "checkout", "2.0.0")
        rc, out = self.run_script("--yes", answer=latest("2.1.0"))
        self.assertEqual((rc, out["status"]), (0, "manual"))
        self.assertEqual(self.ran, [])

    def test_missing_cli(self) -> None:
        with mock.patch.object(update.clidirs, "which", return_value=None):
            ok, done = update.run_commands([["claude", "plugin", "update", "quick-slide@quick-slide"]])
        self.assertFalse(ok)
        self.assertEqual(done[0]["reason"], "not_found")


class OutdatedServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self)
        self.repo = plugin_at(self.tmp / "plugin", "2.1.0")

    def swap(self, running: dict | None, *, codex: bool = False):
        calls: list = []
        with mock.patch.object(start.common, "_running_server", return_value=running), \
                mock.patch.object(start.common, "inside_codex", return_value=codex), \
                mock.patch.object(start.subprocess, "run", side_effect=lambda *a, **k: calls.append(a) or mock.Mock(
                    returncode=0, stdout=json.dumps({"ok": True, "stopped": True}))):
            got = start.replace_outdated_server(self.repo, self.tmp / "home", 5.0)
        return got, calls

    def test_versions(self) -> None:
        self.assertTrue(start.is_newer("2.1.0", "2.0.0"))
        self.assertTrue(start.is_newer("2.1.0", "2.1.0-rc.1"))
        self.assertFalse(start.is_newer("2.0.0", "2.0.0"))
        self.assertFalse(start.is_newer("2.0.0", "2.0.0a2"))   # 版の形でないものとは比べない

    def test_older_idle_server_is_stopped(self) -> None:
        got, calls = self.swap({"version": "2.0.0", "busy": 0})
        self.assertEqual(got, {"from": "2.0.0", "to": "2.1.0", "replaced": True})
        self.assertEqual(calls[0][0][-1], "stop")

    def test_busy_server_is_kept(self) -> None:
        got, calls = self.swap({"version": "2.0.0", "busy": 2})
        self.assertEqual(got, {"from": "2.0.0", "to": "2.1.0", "replaced": False, "busy": 2})
        self.assertEqual(calls, [])

    def test_same_or_newer_server_and_codex_are_left_alone(self) -> None:
        for running, codex in (({"version": "2.1.0", "busy": 0}, False), ({"version": "3.0.0", "busy": 0}, False),
                               (None, False), ({"version": "2.0.0", "busy": 0}, True)):
            got, calls = self.swap(running, codex=codex)
            self.assertIsNone(got, (running, codex))
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
