"""デザインテンプレートのリソースが、この機体にあるか（API の resource-check と `--doctor`）。中身は見ない。"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, start_server, temp_dir

from quick_slide import design_templates as dt

SETUP = REPO / "skills" / "quick-slide" / "scripts" / "quick_slide_setup.py"
# どの OS でも絶対パスになる、存在しない置き場
NO_SUCH = Path(tempfile.gettempdir()).resolve() / "qs-design-template-no-such"


def load_setup():
    sys.dont_write_bytecode = True   # 配る物の中に __pycache__ を残さない
    spec = importlib.util.spec_from_file_location("quick_slide_setup_for_test", SETUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TemplateResourceCheckTests(unittest.TestCase):
    def setUp(self):
        self.fx = start_server(self)
        with self.fx.server.store_lock:
            store = self.fx.store
            config = json.loads(json.dumps(store.brand_revision(store.preset_brand_revision_id())["config"]))
            self.bid = store.create_brand("試験のブランド", config)["id"]
        here = temp_dir(self, "qs-resource-here-")
        document = dt.new_document("weekly", self.bid, "週報")
        document["resources"] = [
            {"id": "r1", "name": "画像フォルダ", "kind": "directory", "path": str(here), "usage": ""},
            {"id": "r2", "name": "別の機体の資料", "kind": "file", "path": str(NO_SUCH / "notes.md"), "usage": ""},
        ]
        r = self.fx.post("/api/settings/design-templates", {"brand_id": self.bid, "document": document})
        self.assertEqual(201, r.status, r.body)

    def test_api_reports_only_missing_resources(self):
        got = self.fx.get("/api/settings/design-templates/resource-check")
        self.assertEqual(200, got.status)
        body = got.json()
        self.assertEqual((1, 2), (body["templates"], body["resources"]))
        self.assertEqual([("週報", "別の機体の資料", "file")],
                         [(m["template_name"], m["resource_name"], m["kind"]) for m in body["missing"]])

    def test_doctor_warns_while_the_server_runs(self):
        home = Path(self.fx.tmp) / "home"
        (home / "runtime").mkdir(parents=True, exist_ok=True)
        (home / "runtime" / "server.json").write_text(
            json.dumps({"host": "127.0.0.1", "port": self.fx.port, "url": self.fx.base_url}), encoding="utf-8")
        setup = load_setup()
        rep = setup.Report()
        setup.check_template_resources(rep, home)
        item = rep.get("template_resources")
        self.assertEqual("warn", item["level"])
        self.assertIn("週報: 別の機体の資料", item["detail"])
        self.assertTrue(item["ok"])   # 注意にとどめ、導入の可否は変えない

    def test_doctor_does_not_start_or_open_anything_when_the_server_is_down(self):
        setup = load_setup()
        rep = setup.Report()
        setup.check_template_resources(rep, temp_dir(self, "qs-empty-home-"))
        item = rep.get("template_resources")
        self.assertEqual("ok", item["level"])
        self.assertIn("サーバが動いていない", item["detail"])


if __name__ == "__main__":
    unittest.main()
