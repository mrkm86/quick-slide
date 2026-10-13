"""illust: 挿絵の SVG を確かめて絞る・アイコンを展開する・配色・PNG（Chrome があるときだけ）。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import illust  # noqa: E402
import svgclean  # noqa: E402

GOOD = '<svg viewBox="0 0 960 1080"><rect width="960" height="1080" fill="#e6f1f3"/><circle cx="480" cy="540" r="200" fill="#006f83"/></svg>'


class CleanTests(unittest.TestCase):
    def test_keeps_shapes_and_sets_size(self) -> None:
        c = svgclean.clean(GOOD)
        self.assertEqual((c.problems, c.width, c.height), ([], 960.0, 1080.0))
        self.assertIn("<circle", c.svg)
        self.assertIn('width="960"', c.svg)

    def test_drops_what_could_run_or_reach_outside(self) -> None:
        bad = ('<svg viewBox="0 0 100 100"><script>alert(1)</script><foreignObject><div/></foreignObject>'
               '<image href="http://example.invalid/a.png"/><use href="#a"/><style>*{}</style>'
               '<rect id="r" width="10" height="10" onclick="x()" fill="url(http://example.invalid/x)" style="fill:url(#g)"/>'
               '<a href="http://example.invalid"><rect width="5" height="5"/></a></svg>')
        c = svgclean.clean(bad)
        self.assertEqual(c.problems, [])
        for word in ("script", "foreignObject", "image", "<use", "style>", "onclick", "example.invalid", "<a "):
            self.assertNotIn(word, c.svg, word)
        self.assertIn('style="fill:url(#g)"', c.svg, "文書の中の #id を指す url() は残す")
        self.assertTrue(c.warnings)

    def test_expands_icons(self) -> None:
        c = svgclean.clean('<svg viewBox="0 0 960 960"><icon name="users" x="100" y="200" size="480" fill="#006f83"/></svg>')
        self.assertEqual(c.problems, [])
        self.assertIn("<path", c.svg)
        self.assertIn('transform="translate(100,680) scale(0.5)"', c.svg)
        self.assertIn('fill="#006f83"', c.svg)
        self.assertIn("users", svgclean.icon_names())

    def test_refuses_what_must_be_rewritten(self) -> None:
        cases = {
            "": "空", "<svg><rect/></svg>": "viewBox", '<svg viewBox="0 0 100 1000"><rect/></svg>': "4 倍",
            '<!DOCTYPE svg><svg viewBox="0 0 1 1"/>': "DOCTYPE", "<svg viewBox='0 0 10 10'><rect>": "読めない",
            '<svg viewBox="0 0 10 10"><icon name="nazo"/></svg>': "無いアイコン", '<svg viewBox="0 0 10 10"></svg>': "描くものが無い",
            "<svg viewBox='0 0 10 10'>" + "<rect/>" * 60000 + "</svg>": "長すぎる",
        }
        for text, word in cases.items():
            c = svgclean.clean(text)
            self.assertTrue(c.problems and word in c.problems[0], (text[:40], c.problems))


class PaletteTests(unittest.TestCase):
    def test_palette_follows_the_primary(self) -> None:
        p = illust.palette("#006f83")
        self.assertEqual(p["primary"], "#006f83")
        self.assertEqual(p["accent"], "#006f83", "差し色が無ければ主役の色")
        self.assertNotEqual(p["primary_light"], p["primary"])
        r = subprocess.run([sys.executable, str(SCRIPTS / "illust.py"), "palette", "--primary", "nazo"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / "illust.py"), *args], input=stdin, capture_output=True, text=True)

    def test_check_and_clean(self) -> None:
        self.assertEqual(self.run_cli("check", "--svg", "-", stdin=GOOD).returncode, 0)
        r = self.run_cli("check", "--svg", "-", stdin="<svg/>")
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stdout.startswith("NG:"))
        self.assertIn("<circle", self.run_cli("clean", "--svg", "-", stdin=GOOD).stdout)

    def test_png(self) -> None:
        chrome = illust.find_chrome(None)
        if chrome is None:
            self.skipTest("Chrome が無い")
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "a.svg", Path(tmp) / "a.png"
            src.write_text(GOOD, encoding="utf-8")
            r = self.run_cli("png", "--svg", str(src), "--out", str(out), "--long", "480")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["viewBox"], [960.0, 1080.0])
            from PIL import Image
            with Image.open(out) as im:
                self.assertEqual(im.size, (427, 480), "長い辺を --long にそろえ、比を保つ")
            r = self.run_cli("png", "--svg", "-", "--out", str(Path(tmp) / "b.png"), "--chrome", "/nonexistent", stdin=GOOD)
            self.assertIn(r.returncode, (0, 4), "渡した在りかが無ければ探す（無ければ 4）")


if __name__ == "__main__":
    unittest.main()
