"""取り込んだページ（PPTX から）に挿絵を重ねる（docs/adr/0013-overlay-illustrations.md）。

- ページの HTML から箱（位置・大きさ・中の文字）を拾い、台本係が空いている場所・大きさ・SVG を決める
- 文字の箱に重なりすぎる置き方は書き直させる。台本の版はページの `illustrations` に SVG のまま持つ
- 描く前に PNG にして、ページの HTML の最後に `<img>` として重ねる（元の文字・図形は変えない）
- 取り込んだページで受けるのは「挿絵」を 1 ページずつだけ。ほかの直しはこれまでどおり理由を返す
"""

from __future__ import annotations

import json
import unittest

from helpers import fake_tools, start_server, temp_dir, wait_until_idle, writer_calls

from quick_slide import edit_kinds
from quick_slide.render import illustrations as ILL
from quick_slide.settings import Settings, WorkerSettings

from test_engines_e2e import html_slide_ready
from test_import_pptx import pptx_bytes

HTML = ('<div style="position:absolute;left:0;top:0;width:0;height:0" data-name="Title 1">'
        '<div style="position:absolute;left:72px;top:43px;width:1296px;height:180px"><span>見出し</span></div></div>'
        '<div style="position:absolute;left:0;top:0;width:1920px;height:1080px;background:#fff"></div>'
        '<img src="imported/media/a.png" alt="" style="position:absolute;left:1500px;top:700px;width:300px;height:200px" />')


class UnitTests(unittest.TestCase):
    def test_boxes_are_read_with_absolute_positions(self) -> None:
        boxes = ILL.page_boxes(HTML)
        self.assertEqual([(b["kind"], b["x"], b["y"], b["w"], b["h"], b["text"]) for b in boxes],
                         [("文字", 72, 43, 1296, 180, "見出し"), ("画像", 1500, 700, 300, 200, "")],
                         "ページ全体を覆う背景は除く")

    def test_overlays_become_img_tags_and_the_key_goes(self) -> None:
        view = {"slides": [{"id": "p-001", "type": "html", "html": HTML,
                            "illustrations": [{"image": {"path": "illustrations/k.png"}, "x": 1450, "y": 300, "w": 400, "h": 500}]}]}
        page = ILL.apply_overlays(view)["slides"][0]
        self.assertNotIn("illustrations", page)
        self.assertTrue(page["html"].startswith(HTML), "元の HTML は変えない")
        self.assertIn('<img src="illustrations/k.png" alt="" style="position:absolute;left:1450px;top:300px;width:400px;'
                      'height:500px;object-fit:contain" />', page["html"])
        self.assertIn("illustrations", view["slides"][0], "元の台本は変えない")

    def test_imported_pages_offer_only_the_illustration(self) -> None:
        self.assertEqual([k["kind"] for k in edit_kinds.offered_for_imported("presentation")], ["illustration"])
        self.assertEqual(edit_kinds.offered_for_imported("pitch"), [])


class EndToEndTests(unittest.TestCase):
    def test_an_illustration_is_laid_over_an_imported_page(self) -> None:
        why = html_slide_ready()
        if why:
            self.skipTest(why)
        tools, log = fake_tools(self, mode="overlay_bad_first")
        fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        r = fx.request("POST", "/api/imports?runtime=cli&name=deck.pptx", raw_body=pptx_bytes(temp_dir(self)), session=True)
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        self.assertEqual(wait_until_idle(fx, deck, seconds=300)["state"], "done")
        view = fx.get(f"/api/decks/{deck}").json()
        self.assertEqual([k["kind"] for k in view["imported_edit_kinds"]], ["illustration"])
        sid = view["pages"][0]["slide_id"]
        url = f"/api/decks/{deck}/instructions"
        for body in ({"scope": "page", "slide_id": sid, "text": "", "kind": "text", "options": ["short"]},
                     {"scope": "pages", "slide_ids": [sid], "text": "", "kind": "illustration"}):
            r = fx.post(url, body)
            self.assertEqual((r.status, r.json()["error"]["code"]), (409, "imported_page"), body)

        r = fx.post(url, {"scope": "page", "slide_id": sid, "text": "", "kind": "illustration"})
        self.assertEqual(r.status, 200, r.body)
        run = wait_until_idle(fx, deck, seconds=300)
        self.assertEqual(run["state"], "done", run["message"])
        overlays = [c for c in writer_calls(log) if c.get("what") == "overlay"]
        self.assertEqual(len(overlays), 2, "文字の箱に重ねた置き方は書き直させる")
        self.assertTrue(any("文字の箱" in ln for ln in overlays[1]["ng"]), overlays[1]["ng"])
        with fx.server.store_lock:
            head = fx.store.load_script(fx.store.head_script(deck))
        items = head["slides"][0]["illustrations"]
        self.assertEqual([(i["x"], i["y"], i["w"], i["h"]) for i in items], [(1450, 300, 400, 500)])
        self.assertIn("<svg", items[0]["image"]["svg"], "台本の版は SVG のまま")
        engine = json.loads(fx.root.engine_script_path(deck).read_text(encoding="utf-8"))
        page = engine["slides"][0]
        self.assertNotIn("illustrations", page)
        src = page["html"].split('<img src="illustrations/')[1].split('"')[0]
        self.assertTrue((fx.root.scripts_dir(deck) / "illustrations" / src).is_file())
        self.assertTrue((fx.root.run_dir(deck, run["id"]) / "deck.pptx").is_file())


if __name__ == "__main__":
    unittest.main()
