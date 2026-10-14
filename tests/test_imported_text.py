"""取り込んだページ（PPTX から）の文字を、見た目を保ったまま直す（docs/adr/0014-imported-text-edits.md）。

- ページの HTML のタグの間の文字に番号を振って台本係に渡し、番号ごとの直した文字だけを差し替える
- 元の字数から大きく増やした答えは書き直させる。一覧に出す見出し（title）も同じ文字なら直す
- 札を選ばずに書いた指示も「文字」として受ける。図解はこれまでどおり理由を返す
"""

from __future__ import annotations

import unittest

from helpers import fake_tools, start_server, temp_dir, wait_until_idle, writer_calls

from quick_slide import edit_kinds, importing
from quick_slide.settings import Settings, WorkerSettings

from test_engines_e2e import html_slide_ready
from test_import_pptx import pptx_bytes

HTML = ('<div style="position:absolute;left:10px;top:10px;width:500px;height:80px"><span style="color:#000">見出し &amp; 題</span></div>'
        '<div style="display:flex"><div><span>•</span></div><div><span> 要点 </span><span>の続き</span></div></div>')


class UnitTests(unittest.TestCase):
    def test_segments_are_numbered_and_bullets_marked(self) -> None:
        self.assertEqual([(s["n"], s["text"], s["bullet"]) for s in importing.text_segments(HTML)],
                         [(1, "見出し & 題", False), (2, "•", True), (3, "要点", False), (4, "の続き", False)])

    def test_only_the_numbered_texts_change(self) -> None:
        out = importing.replace_texts(HTML, {1: "新しい <題>", 3: "直した要点"})
        self.assertEqual([s["text"] for s in importing.text_segments(out)], ["新しい <題>", "•", "直した要点", "の続き"])
        self.assertIn('<span style="color:#000">新しい &lt;題&gt;</span>', out, "書式の属性はそのまま・文字はエスケープ")
        self.assertIn("<span> 直した要点 </span>", out, "前後の空白は元のまま")

    def test_imported_pages_offer_text_and_illustration(self) -> None:
        self.assertEqual([k["kind"] for k in edit_kinds.offered_for_imported("presentation")], ["text", "illustration"])


class EndToEndTests(unittest.TestCase):
    def test_texts_of_an_imported_page_are_rewritten_in_place(self) -> None:
        why = html_slide_ready()
        if why:
            self.skipTest(why)
        tools, log = fake_tools(self, mode="imported_text_long_first")
        fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        r = fx.request("POST", "/api/imports?runtime=cli&name=deck.pptx", raw_body=pptx_bytes(temp_dir(self)), session=True)
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        self.assertEqual(wait_until_idle(fx, deck, seconds=300)["state"], "done")
        with fx.server.store_lock:
            before = fx.store.load_script(fx.store.head_script(deck))["slides"][0]
        sid = fx.get(f"/api/decks/{deck}").json()["pages"][0]["slide_id"]
        r = fx.post(f"/api/decks/{deck}/instructions", {"scope": "page", "slide_id": sid, "text": "見出しと要点を言い切って"})
        self.assertEqual(r.status, 200, r.body)
        run = wait_until_idle(fx, deck, seconds=300)
        self.assertEqual(run["state"], "done", run["message"])
        calls = [c for c in writer_calls(log) if c.get("what") == "imported_text"]
        self.assertEqual(len(calls), 2, "大きく増やした答えは書き直させる")
        self.assertTrue(any("大きく増えた" in ln for ln in calls[1]["ng"]), calls[1]["ng"])
        with fx.server.store_lock:
            after = fx.store.load_script(fx.store.head_script(deck))["slides"][0]
        texts = [s["text"] for s in importing.text_segments(after["html"])]
        self.assertIn("取り込みの見出し（直し）", texts)
        self.assertIn("一つ目の要点（直し）", texts)
        self.assertEqual(after["title"], "取り込みの見出し（直し）", "一覧に出す見出しも直す")
        strip = lambda h: [s for s in importing.text_segments(h)]  # noqa: E731
        self.assertEqual(len(strip(after["html"])), len(strip(before["html"])), "文字の数（番号）は変わらない")
        self.assertEqual(importing.replace_texts(after["html"], {}), after["html"])
        page = fx.get(f"/api/decks/{deck}").json()["pages"][0]
        self.assertEqual(page["title"], "取り込みの見出し（直し）")


    def test_an_edited_text_that_overflows_its_box_is_rewritten(self) -> None:
        why = html_slide_ready()
        if why:
            self.skipTest(why)
        tools, log = fake_tools(self, mode="imported_text_overflow_first")
        fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        r = fx.request("POST", "/api/imports?runtime=cli&name=deck.pptx", raw_body=pptx_bytes(temp_dir(self)), session=True)
        deck = r.json()["deck_id"]
        self.assertEqual(wait_until_idle(fx, deck, seconds=300)["state"], "done")
        sid = fx.get(f"/api/decks/{deck}").json()["pages"][0]["slide_id"]
        r = fx.post(f"/api/decks/{deck}/instructions", {"scope": "page", "slide_id": sid, "text": "", "kind": "text",
                                                        "options": ["firm"]})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(wait_until_idle(fx, deck, seconds=300)["state"], "done")
        calls = [c for c in writer_calls(log) if c.get("what") == "imported_text"]
        self.assertEqual(len(calls), 2, "直した文字が箱からはみ出したら書き直させる")
        self.assertTrue(any("text-overflow" in ln for ln in calls[1]["ng"]), calls[1]["ng"])
        with fx.server.store_lock:
            after = fx.store.load_script(fx.store.head_script(deck))["slides"][0]
        self.assertEqual(after["title"], "取り込みの見出し（直し）")


if __name__ == "__main__":
    unittest.main()
