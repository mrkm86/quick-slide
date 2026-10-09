"""tokens/ の試験: 公式トークンの写し・slide.json の中身の整合・slide.css が slide.json から作れる中身と同じこと・コントラストの条件。"""
import json
import unittest

from _paths import DADS, dads_css_vars
import build_slide_css
import keycolors as k

TOK = DADS / "tokens"


def _slide():
    return json.loads((TOK / "slide.json").read_text(encoding="utf-8"))


class OfficialTokens(unittest.TestCase):
    def test_css_has_official_header_and_body(self):
        text = (TOK / "dads.css").read_text(encoding="utf-8")
        self.assertIn("MIT", text.split("*/")[0])
        self.assertIn("Do not edit directly, this file was auto-generated.", text)
        self.assertTrue((DADS / "LICENSE-dads-tokens.txt").read_text(encoding="utf-8").startswith("MIT License"))

    def test_json_is_valid_and_matches_css(self):
        d = json.loads((TOK / "dads.tokens.json").read_text(encoding="utf-8"))
        css = dads_css_vars()
        blue = d["Color"]["Color"]["Primitive"]["Blue"]
        for step, v in blue.items():
            self.assertEqual(css[f"--color-primitive-blue-{step}"], v["$value"])
        elev = d["Size"]["Elevation"]
        self.assertEqual(len(elev), 8)
        self.assertEqual(sorted(d["Size"]["BorderRadius"]), sorted(["4", "6", "8", "12", "16", "24", "32", "Full"]))


class SlideJson(unittest.TestCase):
    def setUp(self):
        self.d = _slide()

    def test_scale_is_dads_plus_declared_extension(self):
        css = dads_css_vars()
        official = {int(round(float(v[:-3]) * 16)) for key, v in css.items() if key.startswith("--font-size-")}
        self.assertEqual(official, {14, 16, 17, 18, 20, 22, 24, 26, 28, 32, 36, 45, 48, 57, 64})
        for s in self.d["type"]["scale"]:
            if s["origin"] == "dads":
                self.assertIn(s["px"], official, s)
            else:
                self.assertEqual(s["origin"], "extension")
                self.assertIn(s["px"], (80, 96, 120, 160, 200))
        self.assertEqual({s["px"] for s in self.d["type"]["scale"] if s["origin"] == "extension"}, {80, 96, 120, 160, 200})
        self.assertEqual([s["px"] for s in self.d["type"]["scale"]], sorted((s["px"] for s in self.d["type"]["scale"]), reverse=True))

    def test_line_heights_are_dads(self):
        self.assertEqual(set(self.d["type"]["line_height"]), {"100", "120", "130", "140", "150", "160", "170", "175"})
        css = dads_css_vars()
        for k_, v in self.d["type"]["line_height"].items():
            self.assertEqual(float(css[f"--line-height-{k_}"]), v)

    def test_roles_use_scale_and_floors(self):
        t = self.d["type"]
        sizes = {s["px"] for s in t["scale"]}
        floors = t["floor"]
        self.assertEqual(floors, {"body": 24, "note": 22, "watermark": 16})
        for name, r in t["roles"].items():
            for key in ("size", "min", "max"):
                self.assertIn(r[key], sizes, f"{name}.{key}")
            self.assertLessEqual(r["min"], r["size"], name)
            self.assertLessEqual(r["size"], r["max"], name)
            self.assertIn(str(r["line"]), t["line_height"], name)
            self.assertGreaterEqual(r["min"], 16, name)
            self.assertIn(r["weight"], ("N", "B"))
        r = t["roles"]
        self.assertEqual(r["body"]["min"], floors["body"])
        self.assertEqual(r["note"]["min"], floors["note"])
        self.assertEqual(r["watermark"]["min"], floors["watermark"])
        self.assertGreaterEqual(r["table-cell"]["min"], floors["body"])
        # 見出し系は常に本文より大きい（本文との違いが視覚的に明確）
        for name in ("heading", "diagram-heading", "cover-title", "statement", "big-number"):
            self.assertGreater(r[name]["min"], r["body"]["max"], name)
        self.assertGreaterEqual(r["box-title"]["min"], r["body"]["size"] - 4)
        # 計画の既定（2 節）
        self.assertEqual((r["heading"]["size"], r["heading"]["weight"], r["heading"]["line"]), (64, "B", 140))
        self.assertEqual((r["diagram-heading"]["size"], r["diagram-heading"]["line"]), (80, 140))
        self.assertEqual((r["kicker"]["size"], r["kicker"]["weight"], r["kicker"]["line"]), (28, "B", 150))
        self.assertEqual((r["subtitle"]["size"], r["subtitle"]["weight"], r["subtitle"]["line"]), (32, "N", 150))
        self.assertEqual((r["note"]["size"], r["note"]["weight"], r["note"]["line"]), (22, "N", 150))

    def test_letter_spacing_matches_dads(self):
        ls = self.d["type"]["letter_spacing_em"]
        for px, v in ls.items():
            self.assertIn(px, [str(s["px"]) for s in self.d["type"]["scale"]])
        # DADS Std: 45=0, 36〜28=1%, 26〜16=2%（Display の 48 以上は 0）
        self.assertEqual((ls["64"], ls["48"], ls["45"], ls["36"], ls["28"], ls["26"], ls["16"]), (0, 0, 0, 0.01, 0.01, 0.02, 0.02))

    def test_spacing_is_multiples_of_8(self):
        sp = self.d["space"]
        self.assertEqual(list(sp["steps"].values()), [8, 16, 24, 40, 64])
        for v in (*sp["steps"].values(), sp["page_margin_x"], sp["page_margin_y"], self.d["grid"]["gutter"], self.d["grid"]["column"]):
            self.assertEqual(v % 8, 0, v)
        self.assertEqual(sp["page_margin_x"], 120)

    def test_grid_adds_up(self):
        g = self.d["grid"]
        self.assertEqual(g["columns"] * g["column"] + (g["columns"] - 1) * g["gutter"], g["width"])
        self.assertEqual(g["width"] + 2 * g["margin"], self.d["canvas"]["width"])
        self.assertEqual((g["column"], g["gutter"], g["width"], g["margin"]), (96, 48, 1680, 120))
        for n, w in g["span"].items():
            self.assertEqual(w, int(n) * g["column"] + (int(n) - 1) * g["gutter"])
        self.assertEqual(g["span"]["12"], g["width"])

    def test_radius_and_elevation_are_dads(self):
        css = dads_css_vars()
        official = {"0", "4", "6", "8", "12", "16", "24", "32"}
        for k_, v in self.d["radius"].items():
            self.assertTrue(k_ in official or k_ == "full", k_)
        for k_, v in self.d["radius_roles"].items():
            self.assertIn(v, [int(x) for x in self.d["radius"].values()], k_)
        self.assertEqual((self.d["radius_roles"]["card"], self.d["radius_roles"]["photo"]), (16, 8))
        for n, v in self.d["elevation"].items():
            self.assertEqual("".join(v.split()), "".join(css[f"--elevation-{n}"].split()), n)
        self.assertEqual(self.d["elevation_roles"], {"flat": "none", "badge": "2"})

    def test_colors_match_dads_tokens(self):
        css = dads_css_vars()
        for name, c in self.d["color"].items():
            ref = c["dads"]
            val = css[ref]
            if val.startswith("rgba"):
                continue  # photo-band: 透過の値は下で別に確かめる
            self.assertEqual(c["hex"], val, f"{name} ({ref})")
        self.assertEqual(self.d["color"]["photo-band"]["hex"], "rgba(0,0,0,0.6)")
        self.assertEqual(css["--color-neutral-opacity-gray-600"].replace(" ", ""), "rgba(0,0,0,0.6)")

    def test_contrast_conditions(self):
        col = {n: c["hex"] for n, c in self.d["color"].items() if c["hex"].startswith("#")}
        self.assertGreaterEqual(len(self.d["contrast"]), 30)
        for rule in self.d["contrast"]:
            r = k.contrast(col[rule["fg"]], col[rule["bg"]])
            self.assertGreaterEqual(r, rule["min"], f"{rule['fg']} / {rule['bg']} = {r:.2f}（{rule['why']}）")

    def test_photo_band_worst_case(self):
        # 写真が真っ白でも、0.6 の黒の帯の上の白い文字は 4.5:1 以上
        worst = round(255 * (1 - 0.6))
        self.assertGreaterEqual(k.contrast("#ffffff", k.to_hex((worst, worst, worst))), 4.5)

    def test_key_colors_equal_ramp_rule(self):
        r = k.ramp_key_colors("cyan")
        col = self.d["color"]
        self.assertEqual(col["key-primary"]["hex"], r["primary"]["hex"])
        self.assertEqual(col["key-secondary"]["hex"], r["secondary"]["hex"])
        self.assertEqual(col["key-tertiary"]["hex"], r["tertiary"]["hex"])
        self.assertEqual(col["key-background"]["hex"], r["background"]["hex"])

    def test_icon_boxes(self):
        for label, box in self.d["icon"]["label_to_box"].items():
            self.assertEqual(box % 8, 0)
            self.assertGreaterEqual(box, int(label))


class SlideCss(unittest.TestCase):
    def test_in_sync_with_json(self):
        self.assertEqual((TOK / "slide.css").read_text(encoding="utf-8"), build_slide_css.build(_slide()))

    def test_no_name_clash_with_official(self):
        import re

        mine = set(re.findall(r"(--[a-z0-9-]+):", (TOK / "slide.css").read_text(encoding="utf-8")))
        official = set(dads_css_vars())
        self.assertFalse(mine & official)
        self.assertTrue(all(n.startswith("--slide-") for n in mine))


if __name__ == "__main__":
    unittest.main()
