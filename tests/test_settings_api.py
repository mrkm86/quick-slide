"""設定の API: ブランド（色・ロゴの登録・角・フッタ）とスライドスタイルの保存・見本、画像の登録。

v1 から形を引き継ぐ。v2 で変えたところ:
- ロゴの角はエンジンの `--logo-position` と同じ 3 つ（右下・左下・右上）。予約枠（`clean_area`）は持たない
- 「使うパターン」の目録は P4 まで「おまかせ」の 1 つだけ
"""

from __future__ import annotations

import copy
import io
import json
import unittest
from unittest.mock import patch

from helpers import start_server

from PIL import Image

from quick_slide import logo as logo_rules
from quick_slide.server import settings_routes as routes


def png(size=(800, 140), kind="PNG"):
    out = io.BytesIO()
    Image.new("RGB", size, (30, 60, 90)).save(out, format=kind)
    return out.getvalue()


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.fx = start_server(self)
        self.catalog = self.fx.get("/api/settings").json()

    def upload(self, size=(800, 140), data=None, headers=None):
        r = self.fx.request("POST", "/api/settings/images", raw_body=png(size) if data is None else data,
                            session=True, headers=headers)
        self.assertEqual(201, r.status, r.body)
        return r.json()

    def brand(self, size=(800, 140)):
        a = self.upload(size)
        c = copy.deepcopy(self.catalog["brand_default"])
        c["logo"]["variant"] = "normal"
        return {"name": "試験ブランド", "config": c, "assets": [{"role": "logo_normal", "asset_id": a["asset_id"]}]}

    def save(self, b, suffix=""):
        r = self.fx.post("/api/settings/brands" + suffix, b)
        self.assertIn(r.status, (200, 201), r.body)
        return r.json()

    def style(self):
        src = self.catalog["styles"][0]
        return {"source_id": src["id"], "expected_revision_id": src["current_revision_id"],
                "name": "自分の型", "config": {k: src["config"][k] for k in routes.STYLE_FIELDS if k in src["config"]}}

    def error(self, response, code, status=400):
        self.assertEqual(response.status, status, response.body)
        e = response.json()["error"]
        self.assertEqual(e["code"], code)
        self.assertTrue(any(ord(c) > 0x2e80 for c in e["message"]))
        for word in ("Traceback", "asset", "revision", "placement", "brand.", "style."):
            self.assertNotIn(word, e["message"])

    # --- 画面が読む形 ---
    def test_catalog_has_every_key_the_screen_reads(self):
        for key in ("brands", "styles", "counts", "brand_default", "logo_control", "tones", "style_dials",
                    "style_default_id", "layout_patterns", "canvas_sizes", "capabilities", "server"):
            self.assertIn(key, self.catalog, key)
        for key in ("brands", "styles"):
            self.assertEqual(len(self.catalog[key]), self.catalog["counts"][key])
        self.assertEqual(self.catalog["counts"], {"brands": 1, "styles": 2})
        self.assertEqual(set(self.catalog["style_dials"]), {"typeface", "density", "visual_ratio"})
        self.assertIn("format", self.catalog["capabilities"])

    def test_logo_control_offers_the_three_engine_positions_without_reserved_frames(self):
        control = self.catalog["logo_control"]
        self.assertEqual(control["key"], "corner")
        self.assertEqual(control["default"], {"corner": "bottom-right"})
        self.assertEqual([c["value"] for c in control["corners"]], ["bottom-right", "bottom-left", "top-right"])
        self.assertEqual([c["label"] for c in control["corners"]], ["右下", "左下", "右上"])
        self.assertTrue(all("clean_area" not in c for c in control["corners"]))
        self.assertEqual(control["canvas"], [1920, 1080])

    def test_layout_patterns_is_the_single_placeholder_until_engine_types(self):
        self.assertEqual([p["value"] for p in self.catalog["layout_patterns"]], ["auto"])
        for s in self.catalog["styles"]:
            self.assertEqual(s["config"]["allowed_layouts"], ["auto"])

    def test_style_api_has_no_old_path(self):
        self.assertEqual(self.fx.get("/api/templates").status, 404)
        response = self.fx.get("/api/styles")
        self.assertEqual(response.status, 200, response.body)
        self.assertEqual(len(response.json()["styles"]), len(self.catalog["styles"]))

    # --- 画像 ---
    def test_upload_bytes_content_type_and_uuid_only(self):
        data = png()
        a = self.upload(data=data, headers={"Content-Type": "text/plain", "X-Filename": "../../logo.svg"})
        r = self.fx.get(a["url"])
        self.assertEqual(r.body, data)
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertEqual(a["bytes"], len(data))
        self.assertNotIn("path", json.dumps(a))
        self.assertEqual(self.fx.get("/assets/" + a["asset_id"] + "/original.png").status, 404)

    def test_image_corrupt_extension_svg_and_animation_refused(self):
        for data in (b'<svg onload="alert(1)"></svg>', b"not an image", png()[:60]):
            self.error(self.fx.request("POST", "/api/settings/images", raw_body=data, session=True), "image_invalid")
        stream = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(stream, format="PNG", save_all=True,
                                               append_images=[Image.new("RGB", (20, 20), "blue")])
        self.error(self.fx.request("POST", "/api/settings/images", raw_body=stream.getvalue(), session=True), "image_animation")

    def test_size_and_pixel_limits(self):
        self.error(self.fx.request("POST", "/api/settings/images", raw_body=b"a" * (routes.MAX_IMAGE_BYTES + 1), session=True),
                   "image_size_limit", 413)
        with patch.object(routes, "MAX_IMAGE_PIXELS", 100):
            self.error(self.fx.request("POST", "/api/settings/images", raw_body=png((11, 10)), session=True),
                       "image_pixel_limit", 413)

    def test_jpeg_webp_preserve_bytes(self):
        for kind in ("JPEG", "WEBP"):
            a = self.upload(data=png(kind=kind))
            r = self.fx.get(a["url"])
            self.assertEqual(r.headers["content-type"], routes.IMAGE_TYPES[kind][1])
            self.assertEqual(r.body, png(kind=kind))

    def test_symlink_upload_parent_refused(self):
        root = self.fx.root.root
        (root / "runtime" / "uploads").rmdir()
        (root / "runtime" / "uploads").symlink_to(root / "config", target_is_directory=True)
        self.error(self.fx.request("POST", "/api/settings/images", raw_body=png(), session=True), "upload_storage", 409)

    # --- ブランド ---
    def test_preview_places_the_logo_in_every_offered_corner(self):
        b = self.brand((800, 140))
        boxes = {}
        for corner in logo_rules.LOGO_POSITIONS:
            b["config"]["logo"]["placement"] = {"corner": corner}
            r = self.fx.post("/api/settings/brands/preview", {k: b[k] for k in ("config", "assets")})
            self.assertEqual(r.status, 200, r.body)
            p = r.json()
            self.assertIsNone(p["clean_area"])
            self.assertEqual(p["canvas"], [1920, 1080])
            self.assertTrue(p["image_url"].startswith("/api/assets/"))
            x, y, w, h = p["logo_xywh"]
            self.assertEqual((w, h), (251, 44), "縦横比を保って 329×44 の枠に収める")
            self.assertTrue(0 <= x and x + w <= 1920 and 0 <= y and y + h <= 1080)
            boxes[corner] = (x, y)
        self.assertGreater(boxes["bottom-right"][0], boxes["bottom-left"][0])
        self.assertLess(boxes["top-right"][1], boxes["bottom-right"][1])

    def test_the_left_top_corner_and_v1_frame_keys_are_refused(self):
        b = self.brand()
        b["config"]["logo"]["placement"] = {"corner": "top-left"}
        self.error(self.fx.post("/api/settings/brands/preview", {k: b[k] for k in ("config", "assets")}), "logo_position")
        b["config"]["logo"]["placement"] = {"corner": "bottom-right", "clean_width_ratio": 0.2}
        self.error(self.fx.post("/api/settings/brands", b), "logo_position")

    def test_small_logo_is_kept_at_its_original_size_and_explained(self):
        out = self.save(self.brand((80, 20)))
        self.assertEqual(out["preview"]["logo_xywh"][2:], [80, 20])
        self.assertTrue(out["preview"]["warnings"])
        self.assertIn("80×20px", out["preview"]["warnings"][0]["message"])

    def test_tall_logo_fits_the_height_of_the_frame(self):
        out = self.save(self.brand((200, 600)))
        self.assertEqual(out["preview"]["logo_xywh"][2:], [15, 44])
        self.assertTrue(out["preview"]["warnings"])

    def test_rename_is_rolled_back_if_revision_insertion_fails(self):
        b = self.brand()
        old = self.save(b)["item"]
        b.update(name="失敗する名前変更", expected_revision_id=old["current_revision_id"])
        from quick_slide.errors import StateError
        with patch.object(self.fx.store, "add_brand_revision", side_effect=StateError("injected")):
            self.error(self.fx.post("/api/settings/brands/" + old["id"], b), "setting_unavailable", 409)
        self.assertEqual(self.fx.store.brand(old["id"])["name"], old["name"])
        self.assertEqual(self.fx.store.brand(old["id"])["current_revision_id"], old["current_revision_id"])

    def test_revision_save_preserves_existing_deck_and_old_config(self):
        b = self.brand()
        old = self.save(b)["item"]
        with self.fx.server.store_lock:
            deck = self.fx.store.create_deck("既存資料", mode="presentation", brand_revision_id=old["current_revision_id"],
                                             style_revision_id=self.fx.store.preset_style_revision_id())
            original = self.fx.store.brand_revision(old["current_revision_id"])
        b["name"] = "変更後"
        b["config"]["colors"]["primary"] = "#abcdef"
        b["expected_revision_id"] = old["current_revision_id"]
        out = self.save(b, "/" + old["id"])["item"]
        self.assertNotEqual(out["current_revision_id"], old["current_revision_id"])
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.deck(deck["id"])["brand_revision_id"], old["current_revision_id"])
            self.assertEqual(self.fx.store.brand_revision(old["current_revision_id"]), original)
        self.assertEqual((out["revision"], out["name"]), (2, "変更後"))
        # 色は保存時に算出一式のスナップショットになる
        self.assertIn("primary_dark", out["config"]["colors"])

    def test_preset_edit_delete_rejected_even_with_invalid_body(self):
        preset = self.catalog["brands"][0]
        self.error(self.fx.post("/api/settings/brands/" + preset["id"], {}), "preset_read_only", 409)
        self.error(self.fx.post("/api/settings/brands/" + preset["id"] + "/delete", {}), "preset_read_only", 409)
        for action in preset["capabilities"].values():
            self.assertFalse(action["available"])
            self.assertIn("複写", action["reason"])
        t = self.catalog["styles"][0]
        self.error(self.fx.post("/api/settings/styles/" + t["id"], {}), "preset_read_only", 409)
        self.error(self.fx.post("/api/settings/styles/" + t["id"] + "/delete", {}), "preset_read_only", 409)

    def test_stale_revision_cannot_overwrite_or_delete(self):
        b = self.brand()
        old = self.save(b)["item"]
        b["expected_revision_id"] = old["current_revision_id"]
        self.save(b, "/" + old["id"])
        self.error(self.fx.post("/api/settings/brands/" + old["id"], b), "setting_changed", 409)
        self.error(self.fx.post("/api/settings/brands/" + old["id"] + "/delete",
                                {"expected_revision_id": old["current_revision_id"]}), "setting_changed", 409)

    def test_invalid_edit_rolls_back_name_and_pointer(self):
        b = self.brand()
        old = self.save(b)["item"]
        b.update(name="保存されてはいけない", expected_revision_id=old["current_revision_id"])
        b["config"]["colors"]["primary"] = "broken"
        self.error(self.fx.post("/api/settings/brands/" + old["id"], b), "invalid_setting")
        row = next(x for x in self.fx.get("/api/settings").json()["brands"] if x["id"] == old["id"])
        self.assertEqual(row, old)

    def test_missing_selected_variant_and_explicit_none(self):
        b = self.brand()
        b["assets"] = []
        self.error(self.fx.post("/api/settings/brands", b), "invalid_setting")
        b["config"]["logo"]["variant"] = "none"
        out = self.save(b)
        self.assertIsNone(out["preview"]["clean_area"])
        self.assertIsNone(out["preview"]["image_url"])
        self.assertIsNone(out["preview"]["logo_xywh"])

    def test_tampered_or_missing_image_refused_preview_and_save(self):
        b = self.brand()
        aid = b["assets"][0]["asset_id"]
        with self.fx.server.store_lock:
            path = self.fx.root.root / self.fx.store.asset(aid)["relative_path"]
        original = path.read_bytes()
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        self.error(self.fx.post("/api/settings/brands", b), "image_unavailable")
        path.unlink()
        self.error(self.fx.post("/api/settings/brands/preview", {k: b[k] for k in ("config", "assets")}), "image_unavailable")

    def test_character_is_not_a_v2_brand_setting(self):
        b = self.brand()
        b["config"]["character"] = {"description": "案内役"}
        self.error(self.fx.post("/api/settings/brands", b), "invalid_setting")

    def test_footer_text_is_kept_and_not_claimed_as_checked(self):
        b = self.brand()
        b["config"]["footer"]["text"] = "© 試験"
        out = self.save(b)
        self.assertEqual(out["item"]["config"]["footer"]["text"], "© 試験")
        self.assertEqual(out["preview"]["copyright"]["status"], "not_checked")
        for c in self.catalog["capabilities"].values():
            self.assertFalse(c["available"])
            self.assertTrue(c["reason"])

    def test_delete_hides_setting_keeps_image_and_revision(self):
        item = self.save(self.brand())["item"]
        r = self.fx.post("/api/settings/brands/" + item["id"] + "/delete", {"expected_revision_id": item["current_revision_id"]})
        self.assertTrue(r.json()["deleted"])
        self.assertNotIn(item["id"], [b["id"] for b in self.fx.get("/api/settings").json()["brands"]])
        self.assertEqual(self.fx.get("/api/assets/" + item["assets"][0]["asset_id"] + "/file").status, 200)
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.brand_revision(item["current_revision_id"])["config"], item["config"])

    def test_preview_writes_no_brand_or_revision(self):
        b = self.brand()
        before = self.fx.get("/api/settings").json()
        self.fx.post("/api/settings/brands/preview", {k: b[k] for k in ("config", "assets")})
        self.assertEqual(before, self.fx.get("/api/settings").json())

    def test_new_brand_from_the_default_shape(self):
        r = self.fx.post("/api/settings/brands", {"name": "新しいブランド", "config": self.catalog["brand_default"], "assets": []})
        self.assertEqual(r.status, 201, r.body)
        self.assertTrue(r.json()["verified"])

    # --- スタイル ---
    def test_style_copy_edit_name_and_origin_immutable(self):
        b = self.style()
        p = self.fx.post("/api/settings/styles/preview", b)
        self.assertEqual(p.status, 200, p.body)
        r = self.fx.post("/api/settings/styles", b)
        self.assertEqual(r.status, 201, r.body)
        old = r.json()["item"]
        origin = old["origin"]
        with self.fx.server.store_lock:
            original = self.fx.store.style_revision(old["current_revision_id"])
        b.pop("source_id")
        b.update(name="編集した型", expected_revision_id=old["current_revision_id"])
        b["config"]["emphasis_prompt"] = "数値を主役にし、比較図を添える。"
        r = self.fx.post("/api/settings/styles/" + old["id"], b)
        self.assertEqual(r.status, 200, r.body)
        new = r.json()["item"]
        self.assertEqual(new["origin"], origin)
        self.assertEqual(new["revision"], 2)
        self.assertEqual(new["config"]["name"], "編集した型")
        self.assertEqual(new["config"]["emphasis_prompt"], b["config"]["emphasis_prompt"])
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.style_revision(old["current_revision_id"]), original)
        # 削除
        r = self.fx.post("/api/settings/styles/" + old["id"] + "/delete", {"expected_revision_id": new["current_revision_id"]})
        self.assertTrue(r.json()["deleted"])

    def test_style_rename_is_rolled_back_on_invalid_changes(self):
        item = self.fx.post("/api/settings/styles", self.style()).json()["item"]
        from quick_slide.errors import ValidationError
        with self.assertRaises(ValidationError):
            self.fx.store.save_style_settings(item["id"], "巻き戻す名前", {"emphasis_prompt": ""})
        row = self.fx.store.style(item["id"])
        self.assertEqual(row["name"], item["name"])
        self.assertEqual(row["current_revision_id"], item["current_revision_id"])

    def test_style_no_blank_unknown_pattern_or_environment_dependency(self):
        self.error(self.fx.post("/api/settings/styles", {"name": "白紙", "config": {}}), "setting_not_found", 404)
        b = self.style()
        b["config"]["emphasis_prompt"] = "画像 /Users/private/secret.png を使う"
        self.error(self.fx.post("/api/settings/styles", b), "invalid_setting")
        b = self.style()
        b["config"]["logo_override"] = {}
        self.error(self.fx.post("/api/settings/styles", b), "unknown_field")
        b = self.style()
        b["config"]["allowed_layouts"] = ["split"]
        self.error(self.fx.post("/api/settings/styles", b), "invalid_setting")
        b["config"]["allowed_layouts"] = []
        self.error(self.fx.post("/api/settings/styles", b), "invalid_setting")

    def test_style_stale_copy_refused(self):
        b = self.style()
        b["expected_revision_id"] = "old"
        self.error(self.fx.post("/api/settings/styles", b), "setting_changed", 409)

    # --- 境界 ---
    def test_settings_shell_routes_return_html(self):
        brand_id = self.catalog["brands"][0]["id"]
        for path in ("/settings", "/settings/styles", "/settings/server", f"/settings/brands/{brand_id}"):
            response = self.fx.get(path)
            self.assertEqual(response.status, 200, (path, response.body))
            self.assertIn("text/html", response.headers["content-type"])
        for bad in ("/settings/brands", "/settings/brands/", "/settings/brands/not-a-uuid", f"/settings/brands/{brand_id}/"):
            self.assertEqual(self.fx.get(bad).status, 404, bad)

    def test_unknown_and_malformed_inputs_are_japanese(self):
        self.error(self.fx.post("/api/settings/brands", {"path": "/private/secret"}), "unknown_field")
        for bad in (None, [], "wrong", {"colors": []}):
            self.error(self.fx.post("/api/settings/brands", {"name": "名前", "config": bad, "assets": []}), "invalid_setting")
        b = self.brand()
        b["name"] = ""
        self.error(self.fx.post("/api/settings/brands", b), "name_required")

    def test_mutations_keep_host_origin_and_session_guards(self):
        for path in ("/api/settings/images", "/api/settings/brands", "/api/settings/brands/preview", "/api/settings/styles"):
            self.error(self.fx.request("POST", path, body={}), "session_required", 403)
            self.error(self.fx.post(path, {}, headers={"Origin": "https://example.com"}), "bad_origin", 403)
            self.error(self.fx.post(path, {}, host="evil.test"), "bad_host", 421)

    def test_settings_and_secrets_not_served_or_modified(self):
        root = self.fx.root
        key = root.hmac_key_path.read_bytes()
        settings = root.settings_path.read_bytes() if root.settings_path.exists() else None
        self.save(self.brand())
        response = self.fx.get("/api/settings").body
        self.assertNotIn(key, response)
        self.assertNotIn(str(root.root).encode(), response)
        self.assertNotIn(b"relative_path", response)
        self.assertEqual(key, root.hmac_key_path.read_bytes())
        self.assertEqual(settings, root.settings_path.read_bytes() if root.settings_path.exists() else None)
        for path in ("/config/settings.json", "/config/hmac.key", "/static/settings.json"):
            self.assertEqual(self.fx.get(path).status, 404)


if __name__ == "__main__":
    unittest.main()
