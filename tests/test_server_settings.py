"""「この機械のどこに開くか」を画面から直せること。

ここで守るのは、書いた通りに保存されること、危ない形は**ファイルに触れずに**断ること、
そして**他人が手で書いたキーを落とさない**こと。設定ファイルは人も直すので、消したら取り返せない。
"""

from __future__ import annotations

import json
import unittest

from helpers import start_server

from quick_slide.settings import Settings


class TheMachineDoorScreenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = start_server(self)
        self.settings_path = self.fx.server.root.settings_path

    def block(self) -> dict:
        return self.fx.get("/api/settings").json()["server"]

    def save(self, host, names=()):
        return self.fx.post("/api/settings/server", {"host": host, "host_names": list(names)})

    def written(self) -> dict:
        return json.loads(self.settings_path.read_text(encoding="utf-8"))

    # --- 見せる ---

    def test_the_screen_is_told_it_is_shut_by_default(self) -> None:
        b = self.block()
        self.assertEqual((b["host"], b["host_names"], b["bind_scope"]), ("127.0.0.1", [], "loopback"))
        self.assertFalse(b["restart_required"])

    def test_the_screen_is_told_the_loopback_names(self) -> None:
        self.assertEqual(sorted(self.block()["loopback_hosts"]), ["127.0.0.1", "::1", "localhost"])

    # --- 書く ---

    def test_an_address_and_its_names_are_written(self) -> None:
        r = self.save("100.100.1.23", ["my-mac", "my-mac.example.net"])
        self.assertEqual(r.status, 200)
        self.assertIs(r.json()["verified"], True)
        written = self.written()["server"]
        self.assertEqual(written["host"], "100.100.1.23")
        self.assertEqual(written["host_names"], ["my-mac", "my-mac.example.net"])
        self.assertEqual(Settings.load(self.settings_path, env={}).server.host_names, ("my-mac", "my-mac.example.net"))

    def test_shutting_it_again_removes_the_names(self) -> None:
        self.save("100.100.1.23", ["my-mac"])
        self.assertEqual(self.save("127.0.0.1").status, 200)
        self.assertNotIn("host_names", self.written()["server"])
        self.assertEqual(self.block()["bind_scope"], "loopback")

    def test_the_screen_is_told_a_restart_is_needed(self) -> None:
        """保存しても、動いているサーバの口は変わらない。そこを黙らない。"""
        self.save("100.100.1.23", ["my-mac"])
        b = self.block()
        self.assertTrue(b["restart_required"])
        self.assertEqual(b["running_host"], self.fx.server.host)
        self.assertNotEqual(b["host"], b["running_host"])

    # --- 断る ---

    def test_the_wide_open_address_is_refused_and_nothing_is_written(self) -> None:
        before = self.settings_path.read_text(encoding="utf-8") if self.settings_path.exists() else None
        r = self.save("0.0.0.0")
        self.assertEqual(r.status, 400)
        self.assertEqual(r.json()["error"]["code"], "bad_bind_address")
        self.assertIn("ぜんぶの口", r.json()["error"]["message"])
        after = self.settings_path.read_text(encoding="utf-8") if self.settings_path.exists() else None
        self.assertEqual(before, after)

    def test_a_name_is_refused_as_the_address(self) -> None:
        r = self.save("my-mac")
        self.assertEqual((r.status, r.json()["error"]["code"]), (400, "bad_bind_address"))

    def test_names_on_the_shut_door_are_refused(self) -> None:
        r = self.save("127.0.0.1", ["my-mac"])
        self.assertEqual((r.status, r.json()["error"]["code"]), (400, "bad_bind_address"))

    def test_an_empty_address_is_refused(self) -> None:
        self.assertEqual(self.save("   ").status, 400)

    def test_a_name_with_a_port_is_refused(self) -> None:
        r = self.save("100.100.1.23", ["my-mac:7801"])
        self.assertEqual((r.status, r.json()["error"]["code"]), (400, "bad_bind_address"))

    def test_an_unknown_field_is_refused(self) -> None:
        r = self.fx.post("/api/settings/server", {"host": "127.0.0.1", "tls": True})
        self.assertEqual(r.status, 400)

    def test_the_port_can_be_written_and_a_bad_one_is_refused(self) -> None:
        r = self.fx.post("/api/settings/server", {"host": "127.0.0.1", "host_names": [], "port": 9999})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.written()["server"]["port"], 9999)
        self.assertEqual(self.block()["port"], 9999)
        for bad in (80, 70000, "9999", True):
            r = self.fx.post("/api/settings/server", {"host": "127.0.0.1", "port": bad})
            self.assertEqual((r.status, r.json()["error"]["code"]), (400, "bad_port"), bad)
        self.assertEqual(self.written()["server"]["port"], 9999)

    # --- 壊さない ---

    def test_what_someone_wrote_by_hand_is_kept(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps({"concurrency": {"ceiling": 8}, "browser": {"mode": "manual"}, "未知の設定": {"x": 1}}),
            encoding="utf-8")
        self.assertEqual(self.save("100.100.1.23", ["my-mac"]).status, 200)
        written = self.written()
        self.assertEqual(written["concurrency"], {"ceiling": 8})
        self.assertEqual(written["browser"], {"mode": "manual"})
        self.assertEqual(written["未知の設定"], {"x": 1})

    def test_the_port_that_was_written_survives(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps({"server": {"port": 7999}}), encoding="utf-8")
        self.assertEqual(self.save("100.100.1.23", ["my-mac"]).status, 200)
        self.assertEqual(self.written()["server"]["port"], 7999)


if __name__ == "__main__":
    unittest.main()
