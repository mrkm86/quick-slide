"""ブラウザの起動に失敗しても、プロセスとプロフィールを残さない。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import helpers  # noqa: F401 — src を import できるようにする
from live import browser


class BrowserStartupTests(unittest.TestCase):
    def launch(self, script):
        real_spawn = browser.oscompat.popen_group
        children, profiles = [], []

        def spawn(cmd, **kwargs):
            profiles.append(Path(next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("--user-data-dir="))))
            child = real_spawn([sys.executable, "-c", script], **kwargs)
            children.append(child)
            self.addCleanup(browser.oscompat.kill_group, child)
            return child

        return spawn, children, profiles

    def test_timeout_reaps_child_and_removes_profile(self):
        spawn, children, profiles = self.launch("import time; time.sleep(60)")
        with patch.object(browser, "chrome_path", return_value=sys.executable), \
                patch.object(browser.oscompat, "popen_group", side_effect=spawn), \
                patch.object(browser.Browser, "_wait_for_port", side_effect=RuntimeError("startup timeout")):
            with self.assertRaisesRegex(RuntimeError, "startup timeout"):
                browser.Browser()
        self.assertIsNotNone(children[0].poll())
        self.assertFalse(profiles[0].exists())

    def test_early_exit_reports_stderr_and_removes_profile(self):
        spawn, children, profiles = self.launch("import sys; print('startup diagnostic', file=sys.stderr); sys.exit(3)")
        with patch.object(browser, "chrome_path", return_value=sys.executable), \
                patch.object(browser.oscompat, "popen_group", side_effect=spawn):
            with self.assertRaisesRegex(RuntimeError, "(?s)exit 3.*startup diagnostic"):
                browser.Browser()
        self.assertIsNotNone(children[0].poll())
        self.assertFalse(profiles[0].exists())


if __name__ == "__main__":
    unittest.main()
