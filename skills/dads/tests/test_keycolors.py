"""scripts/keycolors.py の試験（unittest・標準ライブラリだけ）。

    python3 -m unittest discover -s skills/dads/tests -v
"""
import io
import json
import math
import pathlib
import subprocess
import sys
import unittest
from contextlib import redirect_stdout

from _paths import DADS, dads_css_vars
import keycolors as k


class Contrast(unittest.TestCase):
    def test_extremes(self):
        self.assertAlmostEqual(k.contrast("#000000", "#ffffff"), 21.0, places=6)
        self.assertAlmostEqual(k.contrast("#ffffff", "#ffffff"), 1.0, places=6)

    def test_order_does_not_matter(self):
        self.assertEqual(k.contrast("#333333", "#ffffff"), k.contrast("#ffffff", "#333333"))

    def test_dads_gray_thresholds(self):
        # DADS のカラー: Gray-420 が白に 3:1、Gray-536 が白に 4.5:1 を確保できる最初の段
        self.assertGreaterEqual(k.contrast("#949494", "#ffffff"), 3.0)
        self.assertGreaterEqual(k.contrast("#767676", "#ffffff"), 4.5)
        self.assertLess(k.contrast("#999999", "#ffffff"), 3.0)  # 400 は 3:1 を割る
        self.assertLess(k.contrast("#7f7f7f", "#ffffff"), 4.5)  # 500 は 4.5:1 を割る

    def test_short_hex_and_errors(self):
        self.assertEqual(k.norm_hex("#FFF"), "#ffffff")
        self.assertEqual(k.norm_hex("0db4f2"), "#0db4f2")
        for bad in ("#12", "#gggggg", "", "#1234567"):
            with self.assertRaises(ValueError):
                k.norm_hex(bad)


class OklchRoundTrip(unittest.TestCase):
    def test_roundtrip(self):
        for hx in ("#0db4f2", "#ff7628", "#333333", "#ffffff", "#000000", "#8843e1"):
            L, c, h = k.to_oklch(hx)
            back = k.from_oklch(L, c, h)
            self.assertLessEqual(max(abs(a - b) for a, b in zip(k.parse_hex(hx), k.parse_hex(back))), 1, hx)


class BrandKeyColors(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(k.brand_key_colors("#0db4f2"), k.brand_key_colors("#0db4f2"))

    def test_light_brand_is_darkened_same_hue(self):
        r = k.brand_key_colors("#0db4f2")
        self.assertTrue(r["brand_changed"])
        p = r["primary"]
        self.assertGreaterEqual(k.contrast(p["hex"], "#ffffff"), 4.5)
        self.assertGreaterEqual(k.contrast(p["hex"], r["background"]["hex"]), 4.5)  # 淡い地の箱に載せても読める
        # 色相は保つ（8bit の丸めの分だけの差）
        h0, h1 = k.to_oklch("#0db4f2")[2], k.to_oklch(p["hex"])[2]
        self.assertLess(abs((h0 - h1 + 180) % 360 - 180), 3.0)
        # 暗くなる（明度だけを動かす）
        self.assertLess(k.to_oklch(p["hex"])[0], k.to_oklch("#0db4f2")[0])

    def test_set_relations(self):
        r = k.brand_key_colors("#0db4f2")
        L = lambda key: k.to_oklch(r[key]["hex"])[0]
        self.assertGreaterEqual(k.contrast(r["secondary"]["hex"], "#ffffff"), 3.0)
        self.assertLess(k.contrast(r["secondary"]["hex"], "#ffffff"), 3.2)  # 3:1 のうち地にいちばん近い
        self.assertGreater(L("secondary"), L("primary"))  # 明
        self.assertLess(L("tertiary"), L("primary"))  # 暗
        self.assertGreaterEqual(k.contrast(r["tertiary"]["hex"], "#ffffff"), 7.0)
        self.assertGreaterEqual(k.contrast(r["on_primary"]["hex"], r["primary"]["hex"]), 4.5)
        self.assertEqual(r["on_primary"]["hex"], "#ffffff")  # 白が 4.5:1 を満たすなら白（DADS の塗りボタンのラベル）
        self.assertTrue(r["background"]["ok"])
        self.assertTrue(all(r[x]["ok"] for x in ("primary", "secondary", "tertiary", "on_primary")))

    def test_compliant_brand_is_kept(self):
        r = k.brand_key_colors("#0017c1")  # DADS の blue-900
        self.assertFalse(r["brand_changed"])
        self.assertEqual(r["primary"]["hex"], "#0017c1")

    def test_ground_only_option(self):
        a = k.brand_key_colors("#0db4f2", on_background=True)["primary"]["hex"]
        b = k.brand_key_colors("#0db4f2", on_background=False)["primary"]["hex"]
        self.assertGreaterEqual(k.to_oklch(b)[0], k.to_oklch(a)[0])  # 地だけのほうが、暗くしなくてよい
        self.assertGreaterEqual(k.contrast(b, "#ffffff"), 4.5)

    def test_dark_ground(self):
        r = k.brand_key_colors("#ffd43d", ground="#1a1a1a")
        self.assertGreaterEqual(k.contrast(r["primary"]["hex"], "#1a1a1a"), 4.5)
        self.assertLess(k.to_oklch(r["secondary"]["hex"])[0], k.to_oklch(r["primary"]["hex"])[0])  # 暗い地では「地に近い」は暗い側
        self.assertGreater(k.to_oklch(r["tertiary"]["hex"])[0], k.to_oklch(r["primary"]["hex"])[0])
        self.assertGreaterEqual(k.contrast(r["text"], r["background"]["hex"]), 4.5)
        self.assertLess(k.to_oklch(r["background"]["hex"])[0], 0.4)  # ごく暗い地

    def test_neutral_brand(self):
        r = k.brand_key_colors("#999999")
        self.assertGreaterEqual(k.contrast(r["primary"]["hex"], "#ffffff"), 4.5)
        self.assertEqual(r["nearest_primitive"]["family"], "solid-gray")

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            k.brand_key_colors("not-a-color")


class Primitives(unittest.TestCase):
    def test_table_matches_official_tokens(self):
        css = dads_css_vars()
        for fam, hexes in k.PRIMITIVE.items():
            self.assertEqual(len(hexes), 13)
            for step, hx in zip(k.STEPS, hexes):
                self.assertEqual(css[f"--color-primitive-{fam}-{step}"], hx, f"{fam}-{step}")
        for step, hx in k.SOLID_GRAY.items():
            self.assertEqual(css[f"--color-neutral-solid-gray-{step}"], hx, f"solid-gray-{step}")
        # 逆向き: 公式の primitive・solid-gray の数が表と同じ（足りない・余る色が無い）
        n = sum(1 for key in css if key.startswith("--color-primitive-")) + sum(
            1 for key in css if key.startswith("--color-neutral-solid-gray-")
        )
        self.assertEqual(n, 13 * 10 + len(k.SOLID_GRAY))

    def test_every_primitive_maps_to_itself(self):
        for fam, step, hx in k._all_primitives():
            r = k.nearest_primitive(hx)
            self.assertEqual((r["family"], r["step"], r["hex"]), (fam, step, hx))
            self.assertEqual(r["delta"], 0.0)

    def test_nearest_primitive_for_brand(self):
        r = k.nearest_primitive("#0db4f2")
        self.assertEqual(r["family"], "light-blue")  # ブランドそのものは light-blue 400〜600 の近く
        self.assertTrue(r["name"].startswith("--color-primitive-light-blue-"))

    def test_nearest_hue_family(self):
        cases = {"#fe3939": "red", "#2cac6e": "green", "#3460fb": "blue", "#ff7628": "orange", "#8843e1": "purple", "#d2a400": "yellow", "#01b7d6": "cyan"}
        for hx, fam in cases.items():
            self.assertEqual(k.nearest_hue(hx)["family"], fam, hx)
        g = k.nearest_hue("#777777")
        self.assertEqual(g["family"], "solid-gray")
        self.assertIsNone(g["hue_gap"])
        self.assertEqual(len(k.nearest_hue("#0db4f2")["ramp"]), 13)

    def test_ramp(self):
        self.assertEqual(len(k.ramp("cyan")), 13)
        self.assertEqual(k.ramp("cyan")[8], {"step": 800, "hex": "#008299"})
        with self.assertRaises(ValueError):
            k.ramp("pink")


class RampKeyColors(unittest.TestCase):
    def test_default_theme_cyan(self):
        # 既定のテーマ（シアン系・プリミティブの段そのまま）。tokens/slide.json の key-* と同じ段になる
        r = k.ramp_key_colors("cyan")
        self.assertEqual((r["primary"]["step"], r["secondary"]["step"], r["tertiary"]["step"], r["background"]["step"]), (900, 600, 1000, 50))
        self.assertEqual(r["on_primary"]["hex"], "#ffffff")
        slide = json.loads((DADS / "tokens" / "slide.json").read_text(encoding="utf-8"))["color"]
        self.assertEqual(
            [r[x]["hex"] for x in ("primary", "secondary", "tertiary", "background")],
            [slide[x]["hex"] for x in ("key-primary", "key-secondary", "key-tertiary", "key-background")],
        )

    def test_all_hues_meet_contrast(self):
        for fam in k.HUES + ("solid-gray",):
            r = k.ramp_key_colors(fam)
            self.assertGreaterEqual(r["primary"]["contrast_on_ground"], 4.5, fam)
            self.assertGreaterEqual(r["primary"]["contrast_on_background"], 4.5, fam)
            self.assertGreaterEqual(r["secondary"]["contrast_on_ground"], 3.0, fam)
            self.assertGreaterEqual(r["on_primary"]["contrast_on_primary"], 4.5, fam)

    def test_unknown_family(self):
        with self.assertRaises(ValueError):
            k.ramp_key_colors("pink")


class Cli(unittest.TestCase):
    SCRIPT = str(DADS / "scripts" / "keycolors.py")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", self.SCRIPT, *args], capture_output=True, text=True)

    def test_json_output_for_brand(self):
        p = self.run_cli("#0db4f2")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        for key in ("primary", "secondary", "tertiary", "background", "on_primary", "nearest_primitive", "hue_family"):
            self.assertIn(key, d)
        self.assertEqual(d["brand"], "#0db4f2")
        self.assertEqual(p.stdout, self.run_cli("#0db4f2").stdout)  # 決定的

    def test_ramp_and_contrast_options(self):
        self.assertEqual(json.loads(self.run_cli("--ramp", "cyan").stdout)["primary"]["step"], 900)
        self.assertEqual(json.loads(self.run_cli("--contrast", "#000", "#fff").stdout)["ratio"], 21.0)

    def test_errors_return_2(self):
        self.assertEqual(self.run_cli("#zzz").returncode, 2)
        self.assertEqual(self.run_cli().returncode, 2)

    def test_main_in_process(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(k.main(["#0db4f2", "--ground", "#ffffff"]), 0)
        self.assertEqual(json.loads(buf.getvalue())["ground"], "#ffffff")


if __name__ == "__main__":
    unittest.main()
