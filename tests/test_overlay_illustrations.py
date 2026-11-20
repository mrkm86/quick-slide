"""取り込んだページ（PPTX から）に挿絵を重ねる（docs/adr/0013-overlay-illustrations.md）。

- ページの HTML から箱（位置・大きさ・中の文字）を拾い、台本係が空いている場所・大きさ・SVG を決める
- 文字の箱に重なりすぎる置き方は書き直させる。台本の版はページの `illustrations` に SVG のまま持つ
- 描く前に PNG にして、ページの HTML の最後に `<img>` として重ねる（元の文字・図形は変えない）
- 取り込んだページで受けるのは「挿絵」と「文字」（ADR 0014）を 1 ページずつだけ。ほかの直しはこれまでどおり理由を返す
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
        self.assertEqual([k["kind"] for k in edit_kinds.offered_for_imported("presentation")],
                         ["text", "diagram", "illustration", "image"])
        self.assertEqual(edit_kinds.offered_for_imported("pitch"), [])


def _box(x, y, w, h, inner, *, justify="flex-start", pad="7.2px 14.4px 7.2px 14.4px"):
    return (f'<div style="position:absolute;left:0;top:0;width:0;height:0"><div style="position:absolute;left:{x}px;top:{y}px;'
            f'width:{w}px;height:{h}px;padding:{pad};display:flex;flex-direction:column;justify-content:{justify}">{inner}</div></div>')


def _para(text, size=48, align="left"):
    return (f'<div style="margin:0px 0 16px 0;text-align:{align};font-size:{size}px;line-height:1.2;white-space:pre-wrap;flex:none">'
            f'<span style="font-size:{size}px">{text}</span></div>')


class FreeSpaceTests(unittest.TestCase):
    """空いている所は、文字の箱の大きさでなく、文字が実際に占める範囲で見る（docs/adr/0038-overlay-free-space.md）。"""

    def test_a_tall_text_box_with_three_lines_is_tightened(self) -> None:
        html = _box(115, 230, 1685, 691, _para("短い 1 行目") + _para("短い 2 行目") + _para("短い 3 行目"))
        (b,) = ILL.page_boxes(html)
        self.assertEqual((b["kind"], b["x"], b["y"]), ("文字", 115, 230))
        self.assertLess(b["h"], 300, "3 行ぶんの高さに詰まる（箱は 691）")
        self.assertLess(b["w"], 600, "折り返さない行は、横も詰まる（箱は 1685）")

    def test_wrapped_text_keeps_the_width_and_counts_the_lines(self) -> None:
        html = _box(100, 200, 500, 800, _para("あ" * 30))   # 48px × 30 字 = 1440px。内側 471px に 4 行（余裕を見て）
        (b,) = ILL.page_boxes(html)
        self.assertEqual(b["w"], 500, "折り返す箱の横は詰めない")
        self.assertTrue(4 * 57 < b["h"] < 400, b)

    def test_the_anchor_follows_the_alignment(self) -> None:
        (b,) = ILL.page_boxes(_box(100, 200, 1000, 600, _para("下にそろえる"), justify="flex-end"))
        self.assertGreater(b["y"], 600, "下そろえの箱は、下の端に詰まる")
        self.assertEqual(b["y"] + b["h"], 800)
        (b,) = ILL.page_boxes(_box(100, 200, 1000, 600, _para("真ん中", align="center"), justify="center"))
        self.assertAlmostEqual(b["x"] + b["w"] / 2, 600, delta=2)
        self.assertAlmostEqual(b["y"] + b["h"] / 2, 500, delta=2)

    def test_boxes_that_cannot_be_estimated_stay_as_they_are(self) -> None:
        table = ('<div style="position:absolute;left:0;top:0;width:0;height:0"><table style="position:absolute;left:100px;top:200px;'
                 'width:900px;height:500px"><tr><td>表の文字</td></tr></table></div>')
        (b,) = ILL.page_boxes(table)
        self.assertEqual((b["x"], b["y"], b["w"], b["h"]), (100, 200, 900, 500))
        loose = _box(100, 200, 900, 500, "段落の外の文字")
        (b,) = ILL.page_boxes(loose)
        self.assertEqual((b["w"], b["h"]), (900, 500), "段落として読めない箱は、箱の大きさのまま")

    def test_a_labelled_frame_becomes_a_slot(self) -> None:
        frame = ('<div style="position:absolute;left:0;top:0;width:0;height:0"><div style="position:absolute;left:1152px;top:245px;'
                 'width:648px;height:634px;background:#dde6ec"></div></div>')
        for label in ("（製品の写真）", "画像", "Photo", "【イメージ】"):
            boxes = ILL.page_boxes(frame + _box(1152, 520, 648, 86, _para(label, 26, "center")))
            self.assertEqual([(b["kind"], b["x"], b["y"], b["w"], b["h"]) for b in boxes], [("置き場", 1152, 245, 648, 634)], label)
            self.assertEqual(boxes[0]["label"], label, "印の文字を控える（絵を置いたら消す）")
        # 枠が無く、印の箱そのものが大きければ、それが置き場
        boxes = ILL.page_boxes(_box(1152, 245, 648, 634, _para("写真", 26, "center"), justify="center"))
        self.assertEqual([(b["kind"], b["w"], b["h"]) for b in boxes], [("置き場", 648, 634)])
        # ふつうの文は印にしない
        boxes = ILL.page_boxes(frame + _box(1152, 520, 648, 86, _para("写真で見る導入の事例", 26)))
        self.assertEqual([b["kind"] for b in boxes], ["図形", "文字"])

    def test_the_label_is_cleared_when_a_picture_lands_on_the_slot(self) -> None:
        from quick_slide.worker import edit as worker_edit

        frame = ('<div style="position:absolute;left:0;top:0;width:0;height:0"><div style="position:absolute;left:1152px;top:245px;'
                 'width:648px;height:634px;background:#dde6ec"></div></div>')
        html = frame + _box(1152, 520, 648, 86, _para("（製品の写真）", 26, "center")) + _box(100, 200, 800, 300, _para("本文の（製品の写真）"))
        boxes = ILL.page_boxes(html)
        on_slot = {"x": 1200, "y": 300, "w": 500, "h": 500}
        self.assertEqual(ILL.slot_under(boxes, on_slot)["label"], "（製品の写真）")
        self.assertIsNone(ILL.slot_under(boxes, {"x": 100, "y": 600, "w": 300, "h": 300}))
        data = {"slides": [{"id": "p-001", "type": "html", "html": html}]}
        item = {"image": {"path": "attachments/a.png"}, **on_slot}
        got = worker_edit._add_overlay(data, "p-001", item, worker_edit._slot_label(boxes, on_slot))["slides"][0]
        self.assertNotIn(">（製品の写真）<", got["html"], "置き場の印は消す")
        self.assertIn("本文の（製品の写真）", got["html"], "本文の同じ言葉は消さない")
        self.assertEqual(len(got["illustrations"]), 1)
        kept = worker_edit._add_overlay(data, "p-001", item, "")["slides"][0]
        self.assertIn(">（製品の写真）<", kept["html"], "置き場でない所に置いたら、何も消さない")

    def test_the_prompt_tells_the_slot(self) -> None:
        from pathlib import Path

        from quick_slide.writer import prompt as P

        root = Path(__file__).resolve().parent.parent / "skills"
        boxes = [{"x": 1152, "y": 245, "w": 648, "h": 634, "kind": "置き場", "text": ""}]
        args = {"mode": "presentation", "images": True}
        page = {"id": "p-001", "position": 1, "title": "商品紹介"}
        text = P.build_overlay_prompt(args, page=page, boxes=boxes, instruction="", what="image", skills_dir=root, generate=True)
        self.assertIn("置き場: x=1152 y=245 幅=648 高さ=634", text)
        self.assertIn("置き場があれば、画像はその枠に置く", text)
        plain = P.build_overlay_prompt(args, page=page, boxes=[{**boxes[0], "kind": "図形"}], instruction="", what="image",
                                       skills_dir=root, generate=True)
        self.assertNotIn("置き場があれば", plain)


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
        self.assertEqual([k["kind"] for k in view["imported_edit_kinds"]], ["text", "diagram", "illustration", "image"])
        sid = view["pages"][0]["slide_id"]
        url = f"/api/decks/{deck}/instructions"
        for body in ({"scope": "page", "slide_id": sid, "text": "", "kind": "image", "options": ["generate"]},
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
        self.assertEqual([(i["x"], i["y"], i["w"], i["h"]) for i in items], [(1450, 60, 220, 220)])
        self.assertIn("<svg", items[0]["image"]["svg"], "台本の版は SVG のまま")
        engine = json.loads(fx.root.engine_script_path(deck).read_text(encoding="utf-8"))
        page = engine["slides"][0]
        self.assertNotIn("illustrations", page)
        src = page["html"].split('<img src="illustrations/')[1].split('"')[0]
        self.assertTrue((fx.root.scripts_dir(deck) / "illustrations" / src).is_file())
        self.assertTrue((fx.root.run_dir(deck, run["id"]) / "deck.pptx").is_file())


if __name__ == "__main__":
    unittest.main()
