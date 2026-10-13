"""ブランド配色の算出・スナップショットの性質（palette.py は v1 から写した。算出規則の正本）。"""
from __future__ import annotations

import colorsys
import copy
import re
import unittest
from unittest.mock import patch

from helpers import REPO, start_server

from quick_slide import palette
from quick_slide.palette import _contrast_ratio, derive
from quick_slide.store.brands import validate_brand_config

HEX = re.compile(r"^#[0-9A-F]{6}$")


def hue(color: str) -> float:
    rgb = tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))
    return colorsys.rgb_to_hls(*rgb)[0]


def color(hue: int, lightness: float) -> str:
    red, green, blue = colorsys.hls_to_rgb(hue / 360, lightness, 0.72)
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in (red, green, blue))


class PaletteTests(unittest.TestCase):
    def setUp(self):
        self.fx = start_server(self)

    def simple_config(self):
        config = copy.deepcopy(self.fx.get("/api/settings").json()["brand_default"])
        config.pop("fonts")
        config["colors"].pop("semantic", None)
        config["colors"].pop("gray", None)
        return config

    def test_t2_t3_deterministic_and_valid_derived_palette(self):
        results = [palette.derive("#F5A623", "#399DD1") for _ in range(100)]
        self.assertTrue(all(result == results[0] for result in results))
        derived = results[0]["derived"]
        colors = [derived[key] for key in ("primary_light", "primary_dark", "border", "surface", "base")]
        colors.extend(derived["gray"])
        colors.extend(derived["semantic"].values())
        self.assertTrue(all(HEX.fullmatch(color) for color in colors))
        self.assertEqual(derived["base"], "#FFFFFF")
        self.assertGreater(hue(derived["semantic"]["success"]), 0.25)
        self.assertLess(hue(derived["semantic"]["success"]), 0.48)
        self.assertTrue(hue(derived["semantic"]["error"]) < 0.04 or hue(derived["semantic"]["error"]) > 0.96)
        primary = tuple(int("F5A623"[index:index + 2], 16) for index in (0, 2, 4))
        border = tuple(int(derived["border"][index:index + 2], 16) for index in (1, 3, 5))
        self.assertGreater(sum((a - b) ** 2 for a, b in zip(primary, border, strict=True)) ** 0.5, 60)

    def test_t4_explicit_overrides_win_and_are_reported(self):
        result = palette.derive("#F5A623", "#399DD1", overrides={
            "border": "#123456", "gray": ["#010203"], "semantic": {"success": "#167A3A"},
        })
        self.assertEqual(result["derived"]["border"], "#123456")
        self.assertEqual(result["derived"]["gray"], ["#010203"])
        self.assertEqual(result["derived"]["semantic"]["success"], "#167A3A")
        self.assertEqual(result["overridden"], ["border", "gray", "semantic.success"])

    def test_fonts_semantic_gray_optional_and_footer_center_allowed(self):
        config = self.simple_config()
        config["footer"]["style"]["position"] = "footer_center"
        validate_brand_config(config, [])

    def test_t5_save_snapshots_and_keeps_old_revision_independent_of_future_rule(self):
        config = self.simple_config()
        saved = self.fx.post("/api/settings/brands", {"name": "算出保存", "config": config, "assets": []})
        self.assertEqual(saved.status, 201, saved.body)
        item = saved.json()["item"]
        old_revision_id = item["current_revision_id"]
        old_colors = copy.deepcopy(item["config"]["colors"])
        for key in ("primary_light", "primary_dark", "border", "surface", "base", "gray", "semantic"):
            self.assertIn(key, old_colors)

        changed = copy.deepcopy(config)
        changed["colors"]["primary"] = "#123456"
        fake = palette.derive("#123456", "#4a4a4a")
        fake["derived"]["border"] = "#010203"
        with patch.object(palette, "derive", return_value=fake):
            updated = self.fx.post("/api/settings/brands/" + item["id"], {
                "name": "算出保存", "config": changed, "assets": [], "expected_revision_id": old_revision_id,
            })
        self.assertEqual(updated.status, 200, updated.body)
        self.assertEqual(updated.json()["item"]["config"]["colors"]["border"], "#010203")
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.brand_revision(old_revision_id)["config"]["colors"], old_colors)

    def test_t6_derive_is_only_used_through_palette_module(self):
        for relative in ("src/quick_slide/store/brands.py", "src/quick_slide/server/settings_routes.py"):
            source = (REPO / relative).read_text(encoding="utf-8")
            self.assertNotRegex(source, r"#[0-9a-fA-F]{6}")
            self.assertNotIn("colorsys", source)
        routes_source = (REPO / "src/quick_slide/server/settings_routes.py").read_text(encoding="utf-8")
        self.assertIn("palette.derive_from_colors", routes_source)
        self.assertIn("palette.snapshot_colors", routes_source)

    def test_t7_preview_includes_palette_and_non_guarantee_note_without_fonts(self):
        response = self.fx.post("/api/settings/brands/preview", {"config": self.simple_config(), "assets": []})
        self.assertEqual(response.status, 200, response.body)
        payload = response.json()["palette"]
        self.assertEqual(payload["input"], {"primary": "#0DB4F2", "accent": "#0DB4F2", "info": None})
        self.assertEqual(payload["note"], "配色は算出値です。生成される絵で実際にこの色が使われる保証はありません。")

    def test_preview_gives_the_colors_the_presentation_engine_paints(self):
        """見本の色（slide.roles）は、描く回と同じテーマからエンジンの deck.palette が作る色と同じ。"""
        config = self.simple_config()
        config["colors"] = {"primary": "#F5A623", "accent": "#399DD1"}
        response = self.fx.post("/api/settings/brands/preview", {"config": config, "assets": []})
        self.assertEqual(response.status, 200, response.body)
        slide = response.json()["slide"]
        self.assertIsNone(slide["error"])
        self.assertEqual(slide["problems"], [])
        engine = _engine_palette({k: v.lower() for k, v in response.json()["theme"].items()})
        for role, value in slide["roles"].items():
            self.assertEqual(value, engine[role], role)
        # 橙は白の地で 4.5:1 に届かないので、同じ色相のまま濃くした色を使う（そのことを画面に伝える）
        self.assertEqual([a["role"] for a in slide["adjusted"]], ["primary", "accent"])
        self.assertNotEqual(slide["roles"]["primary"], "#f5a623")
        self.assertGreaterEqual(_contrast_ratio(slide["roles"]["primary"].upper(), "#FFFFFF"), 4.5)

    def test_reading_the_engine_palette_leaves_no_module_or_bytecode_behind(self):
        import sys

        from quick_slide import slide_palette

        folders = (slide_palette.DECK_PALETTE.parent, slide_palette.KEYCOLORS.parent)
        before = {folder: (folder / "__pycache__").exists() for folder in folders}
        # ほかの試験が先に keycolors を読んでいることがある。読む前と同じ状態に戻ること（足さない・差し替えない）
        modules = {name: sys.modules.get(name) for name in ("keycolors", "deck_palette")}
        slide_palette._module = None      # 読み直させる
        slide_palette.slide_colors({"primary": "#0DB4F2", "accent": "#0DB4F2"})
        for name, module in modules.items():
            self.assertIs(sys.modules.get(name), module, name)
        for folder in folders:
            self.assertEqual((folder / "__pycache__").exists(), before[folder], folder)

    def test_the_text_colors_a_person_chose_reach_the_engine_and_unreadable_ones_are_reported(self):
        from quick_slide import slide_palette

        colors = {"primary": "#F5A623", "accent": "#399DD1", "ink": "#1A1A1A", "muted": "#555555"}
        self.assertEqual({k: palette.snapshot_colors(colors)[k] for k in ("ink", "muted")}, {"ink": "#1A1A1A", "muted": "#555555"})
        theme = palette.engine_theme(palette.snapshot_colors(colors))
        self.assertEqual((theme["ink"], theme["muted"]), ("#1A1A1A", "#555555"))
        self.assertNotIn("muted", palette.engine_theme(palette.snapshot_colors({"primary": "#F5A623", "accent": "#399DD1"})),
                         "決めていない補足の色は渡さない（エンジンが DADS の Gray-600 を使う）")
        out = slide_palette.slide_colors(colors)
        self.assertEqual(out["problems"], [])
        self.assertEqual((out["roles"]["ink"], out["roles"]["muted"]), ("#1a1a1a", "#555555"))
        self.assertTrue(out["overrides"]["ink"]["applied"] and out["overrides"]["muted"]["applied"])
        # 読めない補足の色は、エンジンの検査で問題として返る（画面は保存させない）
        pale = slide_palette.slide_colors({"primary": "#F5A623", "accent": "#399DD1", "muted": "#DDDDDD"})
        self.assertTrue(pale["problems"])
        # 本文が読みにくくなる面の色はエンジンに渡さず、使っていないことと理由を返す
        dark = slide_palette.slide_colors({"primary": "#F5A623", "accent": "#399DD1", "surface": "#999999"})
        self.assertNotIn("surface", dark["theme"])
        self.assertFalse(dark["overrides"]["surface"]["applied"])
        self.assertTrue(dark["overrides"]["surface"]["reason"])


def _engine_palette(theme: dict) -> dict:
    """エンジンの deck.py の palette()（描く回が使うもの）を子のプロセスで呼ぶ。"""
    import json
    import subprocess
    import sys

    from quick_slide.paths import SKILLS_DIR

    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import deck; "
            "print(json.dumps(deck.palette(json.loads(sys.argv[2]))))")
    out = subprocess.run([sys.executable, "-B", "-c", code, str(SKILLS_DIR / "html-slide" / "scripts"), json.dumps(theme)],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


class PaletteContrastTests(unittest.TestCase):
    def test_semantic_contrast_across_hues_and_lightnesses(self):
        """36 色相 x 3 明度の primary/accent の全組み合わせを検査する。"""
        minimum = {"success": 4.5, "error": 4.5, "info": 4.5, "warning": 3.0}
        lowest = {key: float("inf") for key in minimum}
        colors = [color(hue, lightness) for hue in range(0, 360, 10) for lightness in (0.18, 0.50, 0.82)]
        for primary in colors:
            for accent in colors:
                palette = derive(primary, accent)["derived"]
                for key, threshold in minimum.items():
                    ratio = _contrast_ratio(palette["semantic"][key], palette["base"])
                    lowest[key] = min(lowest[key], ratio)
                    self.assertGreaterEqual(ratio, threshold, (primary, accent, key, palette["semantic"][key], ratio))
        self.assertGreaterEqual(lowest["success"], 4.5)
        self.assertGreaterEqual(lowest["error"], 4.5)
        self.assertGreaterEqual(lowest["info"], 4.5)
        self.assertGreaterEqual(lowest["warning"], 3.0)

    def test_success_and_error_remain_their_expected_hues(self):
        for hue in range(0, 360, 10):
            semantic = derive(color(hue, 0.5), color((hue + 130) % 360, 0.5))["derived"]["semantic"]
            success_hue = colorsys.rgb_to_hls(*(int(semantic["success"][index:index + 2], 16) / 255 for index in (1, 3, 5)))[0]
            error_hue = colorsys.rgb_to_hls(*(int(semantic["error"][index:index + 2], 16) / 255 for index in (1, 3, 5)))[0]
            self.assertGreaterEqual(success_hue, 0.26)
            self.assertLessEqual(success_hue, 0.48)
            self.assertTrue(error_hue <= 0.04 or error_hue >= 0.96)

    def test_same_input_is_deterministic_for_one_hundred_calls(self):
        expected = derive("#F5A623", "#399DD1", info="#E6A814")
        for _ in range(100):
            self.assertEqual(derive("#F5A623", "#399DD1", info="#E6A814"), expected)

