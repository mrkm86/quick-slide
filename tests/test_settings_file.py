"""設定ファイル: browser.mode / browser.attach_wait_seconds、server.host / server.port、QUICK_SLIDE_BROWSER 上書き、
未知キーは無視（P1 で外した v1 の生成の節が書いてあっても読める）。"""

from __future__ import annotations

import json
import unittest

from helpers import start_server, temp_dir

from quick_slide.errors import ValidationError
from quick_slide.settings import BROWSER_MODES, BrowserSettings, Settings


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self)
        self.path = self.tmp / "settings.json"

    def _load(self, raw: dict, env: dict | None = None) -> Settings:
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        return Settings.load(self.path, env=env or {})

    def test_defaults(self) -> None:
        s = Settings.load(self.tmp / "missing.json", env={})
        self.assertEqual((s.browser.mode, s.browser.attach_wait_seconds), ("auto", 8.0))
        self.assertEqual((s.server.host, s.server.port), ("127.0.0.1", 7801))
        self.assertEqual(s.intake.max_manuscript_chars, 200_000)
        self.assertEqual(s.launch.token_ttl_seconds, 900)
        self.assertEqual(BROWSER_MODES, ("auto", "external", "manual"))

    def test_file_values_and_env_override(self) -> None:
        s = self._load({"browser": {"mode": "manual", "attach_wait_seconds": 3}, "server": {"host": "localhost", "port": 7802}})
        self.assertEqual((s.browser.mode, s.browser.attach_wait_seconds, s.server.host, s.server.port), ("manual", 3.0, "localhost", 7802))
        s = self._load({"browser": {"mode": "manual"}}, env={"QUICK_SLIDE_BROWSER": "external"})
        self.assertEqual(s.browser.mode, "external", "環境変数がファイルを上書きする")
        s = self._load({}, env={"QUICK_SLIDE_BROWSER": ""})
        self.assertEqual(s.browser.mode, "auto", "空の環境変数は未設定扱い")

    def test_invalid_values_are_rejected_not_defaulted(self) -> None:
        for raw in ({"browser": {"mode": "integrated"}}, {"browser": {"attach_wait_seconds": 0}}, {"browser": {"attach_wait_seconds": -1}},
                    {"browser": {"attach_wait_seconds": "8"}}, {"browser": {"attach_wait_seconds": 601}}, {"server": {"port": "7801"}},
                    {"server": {"port": 70000}}, {"server": {"host": ""}}, {"server": {"port": True}}, {"browser": []}):
            with self.assertRaises(ValidationError, msg=raw):
                self._load(raw)
        with self.assertRaises(ValidationError):
            self._load({}, env={"QUICK_SLIDE_BROWSER": "yes"})
        with self.assertRaises(ValidationError):
            BrowserSettings(mode="auto", attach_wait_seconds=float("nan"))

    def test_unknown_keys_are_ignored(self) -> None:
        s = self._load({"browser": {"mode": "auto", "future_key": 1}, "server": {"port": 7801, "tls": True}, "totally_new": {"x": 1},
                        "worker": {"supervise": True}, "gate": {"enabled": True}, "concurrency": {"ceiling": 4}})
        self.assertEqual((s.browser.mode, s.server.port), ("auto", 7801))

    def test_non_loopback_host_in_settings_is_not_downgraded_by_the_server(self) -> None:
        from quick_slide.errors import StartupError
        from quick_slide.paths import DataRoot
        from quick_slide.server import QuickSlideServer

        s = self._load({"server": {"host": "0.0.0.0"}})
        self.assertFalse(s.server.is_loopback)
        with self.assertRaises(StartupError):
            QuickSlideServer(DataRoot(self.tmp / "home"), settings=s, port=0)

    def test_health_reports_the_browser_settings_it_actually_loaded(self) -> None:
        from quick_slide.settings import BrowserSettings as B

        fx = start_server(self, settings=Settings(browser=B(mode="external", attach_wait_seconds=2.5)))
        h = fx.get("/api/health").json()
        self.assertEqual(h["browser"], {"mode": "external", "attach_wait_seconds": 2.5})


if __name__ == "__main__":
    unittest.main()
