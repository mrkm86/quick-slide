"""プレゼンの挿絵（画像の枠の `image.svg`。docs/adr/0011-svg-illustrations.md）。

- 台本の挿絵は、エンジンに渡す前に挿絵のスキルで PNG になり、枠は `path` に替わる（台本の版は SVG のまま）
- 同じ挿絵は描き直さない。描けない挿絵は、どのページかを付けた `NG:` で台本係に書き直させる
- 台本係への指示に、挿絵の書き方とテーマの色から作った配色が入る
Chrome（と html-slide の道具）が無ければ、描く試験は飛ばす。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import fake_tools, intake_payload, start_server, wait_until_idle, writer_calls

from quick_slide.engines import ENGINES
from quick_slide.paths import SKILLS_DIR
from quick_slide.render import illustrations as ILL
from quick_slide.worker.tools import Tools
from quick_slide.writer import prompt as P

from test_engines_e2e import html_slide_ready

sys.path.insert(0, str(SKILLS_DIR / "illust" / "scripts"))
import illust  # noqa: E402

SVG = '<svg viewBox="0 0 960 1080"><rect width="960" height="1080" fill="#e6f1f3"/><icon name="users" x="250" y="330" size="460" fill="#006f83"/></svg>'


def deck(svg: str) -> dict:
    return {"title": "t", "slides": [{"id": "p-a", "type": "photo", "headline": "h", "image": {"svg": svg, "focus": "left"}},
                                     {"id": "p-b", "type": "cards", "items": [{"title": "x", "image": {"prompt": "p"}}]}]}


class MaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        if illust.find_chrome(None) is None:
            self.skipTest("Chrome が無い")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_svg_becomes_a_png_path_once(self) -> None:
        data = deck(SVG)
        out, problems = ILL.materialize(data, scripts_dir=self.tmp, tools=Tools(), home=self.tmp)
        self.assertEqual(problems, [])
        image = out["slides"][0]["image"]
        self.assertEqual(set(image), {"path", "focus"}, "svg は path に替わり、ほかのキーは残る")
        png = self.tmp / image["path"]
        self.assertTrue(png.is_file())
        self.assertEqual(out["slides"][1], data["slides"][1], "生成の枠は触らない")
        self.assertIn("svg", data["slides"][0]["image"], "元の台本は変えない")
        stamp = png.stat().st_mtime_ns
        ILL.materialize(data, scripts_dir=self.tmp, tools=Tools(), home=self.tmp)
        self.assertEqual(png.stat().st_mtime_ns, stamp, "同じ挿絵は描き直さない")

    def test_broken_svg_comes_back_as_ng_for_the_page(self) -> None:
        _, problems = ILL.materialize(deck("<svg><rect/></svg>"), scripts_dir=self.tmp, tools=Tools(), home=self.tmp)
        self.assertTrue(problems and problems[0].startswith('NG: id "p-a" の挿絵: ') and "viewBox" in problems[0], problems)


class PromptTests(unittest.TestCase):
    def test_presentation_writer_is_told_how_to_draw_and_which_colors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            theme = Path(tmp) / "theme.yaml"
            theme.write_text('background: "#ffffff"\nink: "#333333"\nprimary: "#AA3300"\n', encoding="utf-8")
            args = {"mode": "presentation", "images": False, "look": {"theme": str(theme)}}
            rules = P._rules(ENGINES["presentation"], args)
            rule = next(r for r in rules if r.startswith("挿絵"))
            self.assertIn("primary #aa3300", rule, "配色はテーマの主役の色から")
            self.assertTrue(any("image に書いてよいのは挿絵（svg）だけ" in r for r in rules), "生成なしでも挿絵は書ける")
            self.assertTrue(any(r.startswith("挿絵") for r in P._edit_rules(ENGINES["presentation"], args)), "直しでも書ける")
        self.assertIn("illust/references/authoring.md", ENGINES["presentation"].contract_docs)
        self.assertFalse(any(r.startswith("挿絵") for r in P._rules(ENGINES["pitch"], {"mode": "pitch"})), "ピッチは丸ごと描く")


class PipelineTests(unittest.TestCase):
    def test_a_presentation_with_an_illustration_is_drawn_and_a_broken_one_is_rewritten(self) -> None:
        why = html_slide_ready()
        if why:
            self.skipTest(why)
        tools, log = fake_tools(self, extra={"FAKE_WRITER_ILLUST": "bad_first"})
        fx = start_server(self, tools=tools)
        r = fx.post("/api/intakes", intake_payload(runtime="codex", mode="presentation"))
        self.assertEqual(r.status, 201, r.body)
        deck_id = r.json()["deck_id"]
        run = wait_until_idle(fx, deck_id, seconds=300)
        self.assertEqual(run["state"], "done", run["message"])
        makes = [c for c in writer_calls(log) if c.get("what") == "make"]
        self.assertEqual(len(makes), 2, "描けない挿絵は台本係に書き直させる")
        self.assertTrue(any('id "p-illust" の挿絵' in ln for ln in makes[1]["ng"]), makes[1]["ng"])
        engine_script = json.loads(fx.root.engine_script_path(deck_id).read_text(encoding="utf-8"))
        page = next(s for s in engine_script["slides"] if s["id"] == "p-illust")
        self.assertTrue(page["image"]["path"].startswith("illustrations/"), page)
        self.assertTrue((fx.root.scripts_dir(deck_id) / page["image"]["path"]).is_file())
        with fx.server.store_lock:
            head = fx.store.load_script(fx.store.head_script(deck_id))
        self.assertIn("svg", next(s for s in head["slides"] if s["id"] == "p-illust")["image"], "台本の版は SVG のまま")


if __name__ == "__main__":
    unittest.main()
