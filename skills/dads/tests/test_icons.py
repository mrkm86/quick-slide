"""icons/ の試験: 名前の数・形・deck_icons.py から引き継いだ 45 種・対応表と JSON が合っていること。"""
import json
import re
import unittest

from _paths import DADS
import build_icons

ICONS = json.loads((DADS / "icons" / "material-symbols.json").read_text(encoding="utf-8"))

# html-slide の scripts/deck_icons.py にある 45 種の名前（意味を引き継ぐ。名前そのものを変えない）
INHERITED_NAMES = (
    "doc list checklist code chart trend scissors image video mic speaker captions chat mail sparkle bulb target flag check cross alert "
    "search folder layers gear shield lock link refresh upload download calendar clock yen user users building laptop phone cloud book pin star rocket hub"
).split()


class Icons(unittest.TestCase):
    def test_count(self):
        self.assertTrue(80 <= len(ICONS) <= 100, len(ICONS))

    def test_inherited_45_are_present(self):
        self.assertEqual(len(INHERITED_NAMES), 45)
        self.assertEqual(set(INHERITED_NAMES), set(build_icons.INHERITED))
        for n in INHERITED_NAMES:
            self.assertIn(n, ICONS)

    def test_shape(self):
        for name, e in ICONS.items():
            self.assertRegex(name, r"^[a-z]+(-[a-z]+)*$")
            self.assertEqual(set(e), {"path", "viewBox", "symbol"}, name)
            self.assertEqual(e["viewBox"], "0 -960 960 960", name)
            self.assertRegex(e["path"], r"^[Mm]", name)
            self.assertNotIn("<", e["path"], name)
            self.assertTrue(re.fullmatch(r"[A-Za-z0-9 .,\-]+", e["path"]), f"{name}: path に想定外の文字")
            self.assertLess(len(e["path"]), 6000, name)

    def test_matches_map(self):
        self.assertEqual(set(ICONS), set(build_icons.MAP))
        for name, (symbol, note) in build_icons.MAP.items():
            self.assertEqual(ICONS[name]["symbol"], symbol)
            self.assertTrue(note)

    def test_symbols_are_unique(self):
        syms = [e["symbol"] for e in ICONS.values()]
        self.assertEqual(len(syms), len(set(syms)))

    def test_semantic_names_exist(self):
        for n in ("info", "notice", "error", "alert", "check", "cross", "help", "arrow-right", "arrow-left", "arrow-up", "arrow-down",
                  "chevron-right", "chevron-down", "close", "external-link", "more-vert", "user", "clock", "doc", "hourglass"):
            self.assertIn(n, ICONS)

    def test_license_files(self):
        self.assertIn("Apache License", (DADS / "icons" / "LICENSE-material-symbols.txt").read_text(encoding="utf-8")[:200])


if __name__ == "__main__":
    unittest.main()
