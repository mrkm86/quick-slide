"""デザインテンプレートのリソースが、この機体にあるか（API の resource-check と `--doctor`）。中身は見ない。"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from helpers import REPO, start_server, temp_dir

from quick_slide import design_templates as dt
from quick_slide.server import template_routes

SETUP = REPO / "skills" / "quick-slide" / "scripts" / "quick_slide_setup.py"
# どの OS でも絶対パスになる、存在しない置き場
NO_SUCH = Path(tempfile.gettempdir()).resolve() / "qs-design-template-no-such"
MISSING_KEYS = {"brand_name", "template_id", "template_name", "resource_name", "kind"}


def load_setup():
    sys.dont_write_bytecode = True   # 配る物の中に __pycache__ を残さない
    spec = importlib.util.spec_from_file_location("quick_slide_setup_for_test", SETUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Base(unittest.TestCase):
    def setUp(self):
        self.fx = start_server(self)
        with self.fx.server.store_lock:
            store = self.fx.store
            config = json.loads(json.dumps(store.brand_revision(store.preset_brand_revision_id())["config"]))
            self.bid = store.create_brand("試験のブランド", config)["id"]
        self.here = temp_dir(self, "qs-resource-here-")

    def create(self, name, resources):
        document = dt.new_document("weekly", self.bid, name)
        document["resources"] = resources
        r = self.fx.post("/api/settings/design-templates", {"brand_id": self.bid, "document": document})
        self.assertEqual(201, r.status, r.body)
        return r.json()

    def present(self, rid="r1"):
        return {"id": rid, "name": "画像フォルダ", "kind": "directory", "path": str(self.here), "usage": ""}

    def absent(self, rid="r2", name="別の機体の資料"):
        return {"id": rid, "name": name, "kind": "file", "path": str(NO_SUCH / "notes.md"), "usage": ""}

    def check(self):
        got = self.fx.get("/api/settings/design-templates/resource-check")
        self.assertEqual(200, got.status, got.body)
        return got.json()


class ResourceCheckApiTests(Base):
    def test_reports_only_missing_resources_without_paths(self):
        self.create("週報", [self.present(), self.absent()])
        body = self.check()
        self.assertEqual({"templates", "resources", "missing"}, set(body))   # パスを返さない
        self.assertEqual((1, 2), (body["templates"], body["resources"]))
        self.assertEqual([MISSING_KEYS], [set(m) for m in body["missing"]])
        self.assertEqual([("週報", "別の機体の資料", "file")],
                         [(m["template_name"], m["resource_name"], m["kind"]) for m in body["missing"]])
        self.assertNotIn(str(NO_SUCH), json.dumps(body, ensure_ascii=False))

    def test_deleted_templates_and_older_versions_are_not_counted(self):
        gone = self.create("消したもの", [self.absent()])
        r = self.fx.post(f"/api/settings/design-templates/{gone['template_id']}/delete",
                         {"expected_revision_id": gone["current_revision_id"]})
        self.assertEqual(200, r.status, r.body)
        kept = self.create("直したもの", [self.absent()])
        document = dict(kept["document"], resources=[self.present()])
        r = self.fx.post(f"/api/settings/design-templates/{kept['template_id']}",
                         {"document": document, "mode": "new_version", "expected_revision_id": kept["current_revision_id"]})
        self.assertEqual(200, r.status, r.body)
        body = self.check()
        self.assertEqual((1, 1, []), (body["templates"], body["resources"], body["missing"]))

    def test_existence_is_checked_outside_the_store_lock(self):
        self.create("週報", [self.absent()])
        lock = self.fx.server.store_lock
        free = []

        def presence(resources):
            # 別のスレッドから鍵が取れるなら、この確認は鍵の外で動いている
            got = []

            def probe():
                ok = lock.acquire(timeout=1)
                if ok:
                    lock.release()   # RLock は取ったスレッドでしか放せない
                got.append(ok)

            t = threading.Thread(target=probe)
            t.start()
            t.join()
            free.append(bool(got and got[0]))
            return [False for _ in resources]

        with mock.patch.object(template_routes.dt, "resource_presence", side_effect=presence):
            self.check()
        self.assertEqual([True], free)


class DoctorTests(Base):
    def write_record(self, home: Path, **overrides):
        instance = self.fx.get("/api/health").json()["instance_id"]
        record = {"host": "127.0.0.1", "port": self.fx.port, "url": "http://not-resolvable.invalid:1/",
                  "instance_id": instance, **overrides}
        (home / "runtime").mkdir(parents=True, exist_ok=True)
        (home / "runtime" / "server.json").write_text(json.dumps(record), encoding="utf-8")

    def item(self, home: Path, setup=None):
        setup = setup or load_setup()
        rep = setup.Report()
        setup.check_template_resources(rep, home)
        return rep.get("template_resources")

    def test_warns_while_the_server_runs_even_if_the_shown_url_does_not_resolve(self):
        self.create("週報", [self.present(), self.absent()])
        home = temp_dir(self, "qs-home-")
        self.write_record(home)   # 表示用の url は引けない名前。記録の host・port に直接つなぐ
        item = self.item(home)
        self.assertEqual("warn", item["level"])
        self.assertIn("週報: 別の機体の資料", item["detail"])
        self.assertTrue(item["ok"])   # 注意にとどめ、導入の可否は変えない

    def test_all_present_is_ok(self):
        self.create("週報", [self.present()])
        home = temp_dir(self, "qs-home-")
        self.write_record(home)
        item = self.item(home)
        self.assertEqual("ok", item["level"])
        self.assertIn("この機体にあります", item["detail"])

    def test_another_instance_on_the_recorded_port_is_not_asked(self):
        self.create("週報", [self.absent()])
        home = temp_dir(self, "qs-home-")
        self.write_record(home, instance_id="someone-else")
        item = self.item(home)
        self.assertEqual("ok", item["level"])
        self.assertIn("サーバが動いていない", item["detail"])

    def test_broken_or_foreign_responses_are_not_read_as_all_present(self):
        home = temp_dir(self, "qs-home-")
        self.write_record(home)
        setup = load_setup()
        health = {"instance_id": self.fx.get("/api/health").json()["instance_id"]}
        for broken in ({}, {"templates": 1, "resources": 1}, {"templates": "1", "resources": 0, "missing": []},
                       {"templates": 1, "resources": 1, "missing": [{"template_name": "週報"}]}, None):
            with self.subTest(broken=broken):
                with mock.patch.object(setup, "_server_get", side_effect=[health, broken]):
                    item = self.item(home, setup)
                self.assertEqual("warn", item["level"])
                self.assertIn("確かめられませんでした", item["detail"])

    def test_corrupt_record_and_down_server_neither_start_nor_open_anything(self):
        setup = load_setup()
        for name, content in (("empty", None), ("corrupt", "{not json"), ("partial", json.dumps({"host": "127.0.0.1"}))):
            with self.subTest(name=name):
                home = temp_dir(self, "qs-home-")
                if content is not None:
                    (home / "runtime").mkdir(parents=True)
                    (home / "runtime" / "server.json").write_text(content, encoding="utf-8")
                fail = AssertionError("--doctor はサーバを起こさず、データベースを開かない")
                with mock.patch.object(subprocess, "Popen", side_effect=fail), \
                     mock.patch.object(subprocess, "run", side_effect=fail), \
                     mock.patch.object(sqlite3, "connect", side_effect=fail):
                    item = self.item(home, setup)
                self.assertEqual("ok", item["level"])
                self.assertIn("サーバが動いていない", item["detail"])


if __name__ == "__main__":
    unittest.main()
