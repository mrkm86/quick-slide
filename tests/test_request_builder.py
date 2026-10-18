"""依頼文の条件だけを記憶し、生成や通常設定を変更しないAPI。"""
from __future__ import annotations

import concurrent.futures
import json
import unittest
from unittest.mock import patch

from helpers import start_server
from quick_slide import design_templates as dt
from quick_slide.store.intakes import MAX_PAGE_COUNT

BASE = "/api/request-builder"


class RequestBuilderTests(unittest.TestCase):
    def setUp(self):
        self.fx = start_server(self, start_workers=False)
        self.initial = self.fx.get(BASE).json()
        self.preferences = self.initial["preferences"]
        self.bid = self.preferences["brand_id"]

    def template(self, modes=None):
        doc = dt.new_document("placeholder", "placeholder", "試験テンプレート")
        doc.update(modes=modes or ["presentation"], instructions="本文を依頼文へ展開しない")
        r = self.fx.post("/api/settings/design-templates", {"brand_id": self.bid, "document": doc})
        self.assertEqual(r.status, 201, r.body)
        return r.json()

    def test_saved_conditions_restore_without_manuscript_or_settings_changes(self):
        self.assertFalse(self.fx.root.request_builder_path.exists())
        original = self.fx.root.settings_path.read_bytes() if self.fx.root.settings_path.exists() else None
        t = self.template()
        value = dict(self.preferences, template_id=t["template_id"], quantity=5, look="images", workflow="direct")
        self.assertEqual(self.fx.post(BASE + "/preferences", value).status, 200)
        self.assertEqual(self.fx.get(BASE).json()["preferences"], value)
        document = json.loads(self.fx.root.request_builder_path.read_text(encoding="utf-8"))
        self.assertEqual(document, {"schema_version": 1, "preferences": value})
        self.assertEqual(self.fx.root.settings_path.read_bytes() if self.fx.root.settings_path.exists() else None, original)
        self.assertEqual(self.fx.get(BASE).headers["cache-control"], "no-store")
        self.assertNotIn("instructions", str(self.fx.get(BASE).json()["templates"]))

    def test_validation_and_guards_reject_without_writing(self):
        t = self.template()
        invalid = [dict(self.preferences, theme="do not save"), dict(self.preferences, quantity=True),
                   dict(self.preferences, quantity=MAX_PAGE_COUNT + 1), dict(self.preferences, quantity=1.5), dict(self.preferences, mode=[]),
                   dict(self.preferences, workflow="conversation"), dict(self.preferences, brand_id="missing"),
                   dict(self.preferences, mode="motion", template_id=t["template_id"]),
                   dict(self.preferences, mode="motion", look="images"),
                   dict(self.preferences, mode="pitch", template_id=t["template_id"])]
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.fx.post(BASE + "/preferences", body).status, 400)
        self.assertEqual(self.fx.request("POST", BASE + "/preferences", self.preferences).status, 403)
        self.assertEqual(self.fx.post(BASE + "/preferences", self.preferences, headers={"Origin": "https://invalid.example"}).status, 403)
        self.assertEqual(self.fx.request("GET", BASE, host="invalid.example").status, 421)
        self.assertFalse(self.fx.root.request_builder_path.exists())

    def test_other_brand_and_deleted_candidates_are_not_silently_saved(self):
        t = self.template()
        catalog = self.fx.get("/api/settings").json()
        other = self.fx.post("/api/settings/brands", {"name": "別のブランド", "config": catalog["brand_default"]}).json()["item"]
        value = dict(self.preferences, template_id=t["template_id"], quantity=7, look="diagrams")
        self.assertEqual(self.fx.post(BASE + "/preferences", dict(value, brand_id=other["id"])).status, 400)
        self.assertEqual(self.fx.post(BASE + "/preferences", value).status, 200)
        self.assertEqual(self.fx.post("/api/settings/design-templates/" + t["template_id"] + "/delete", {"expected_revision_id": t["current_revision_id"]}).status, 200)
        restored = self.fx.get(BASE).json()
        self.assertEqual(restored["preferences"], dict(value, template_id=None))
        self.assertTrue(restored["warning"])
        self.assertEqual(self.fx.post(BASE + "/preferences", value).status, 400)

    def test_corrupt_file_and_failed_replace_preserve_previous_conditions(self):
        path = self.fx.root.request_builder_path
        path.write_bytes(b"{broken")
        self.assertTrue(self.fx.get(BASE).json()["warning"])
        self.assertEqual(self.fx.post(BASE + "/preferences", self.preferences).status, 200)
        previous = path.read_bytes()
        with patch("quick_slide.server.request_builder.oscompat.replace", side_effect=PermissionError):
            response = self.fx.post(BASE + "/preferences", dict(self.preferences, quantity=5))
        self.assertEqual(response.status, 500, response.body)
        self.assertEqual(previous, path.read_bytes())
        self.assertFalse(list(path.parent.glob(".request-builder-*.tmp")))

    def test_deleted_brand_restores_default_and_clears_template(self):
        catalog = self.fx.get("/api/settings").json()
        b = self.fx.post("/api/settings/brands", {"name": "削除するブランド", "config": catalog["brand_default"]}).json()["item"]
        value = dict(self.preferences, brand_id=b["id"], quantity=9, workflow="direct")
        self.assertEqual(self.fx.post(BASE + "/preferences", value).status, 200)
        self.assertEqual(self.fx.post("/api/settings/brands/" + b["id"] + "/delete", {"expected_revision_id": b["current_revision_id"]}).status, 200)
        restored = self.fx.get(BASE).json()
        self.assertEqual(restored["preferences"], dict(value, brand_id=self.bid, template_id=None))
        self.assertTrue(restored["warning"])

    def test_concurrent_saves_leave_complete_json(self):
        # 同じサーバの複数画面。セッショントークンを先に確定させる。
        self.fx.session()
        values = [dict(self.preferences, quantity=i) for i in range(1, 9)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda v: self.fx.post(BASE + "/preferences", v).status, values))
        self.assertEqual(results, [200] * len(values))
        self.assertIn(self.fx.get(BASE).json()["preferences"], values)
