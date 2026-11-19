"""取り込んだページ（PPTX から）の文字を、見た目を保ったまま直す（docs/adr/0014-imported-text-edits.md）。

- ページの HTML のタグの間の文字に番号を振って台本係に渡し、番号ごとの直した文字だけを差し替える
- 元の字数から大きく増やした答えは書き直させる。一覧に出す見出し（title）も同じ文字なら直す
- 札を選ばずに書いた指示も「文字」として受ける。図解はこれまでどおり理由を返す
"""

from __future__ import annotations

import json
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
        self.assertEqual([k["kind"] for k in edit_kinds.offered_for_imported("presentation")],
                         ["text", "diagram", "illustration", "image"])


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


    def test_an_imported_deck_stays_brandless_after_edits_and_copies(self) -> None:
        """取り込んだ資料は「ブランドなし」をデッキが持つ（docs/adr/0035-brandless-decks.md）。直しの回でも複製でも、
        ロゴ・フッタ・ページ番号・透かしを足さず、色は PPTX のテーマを使う。"""
        why = html_slide_ready()
        if why:
            self.skipTest(why)
        tools, _ = fake_tools(self, mode="imported_text")
        fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        with fx.server.store_lock:   # 既定になるブランド（利用者の最新）に、足されたら分かる書式を入れておく
            cfg = json.loads(json.dumps(fx.store.brand_revision(fx.store.preset_brand_revision_id())["config"]))
            cfg["footer"] = {"text": "足されては困るフッタ"}
            cfg["format"] = {**(cfg.get("format") or {}), "page_numbers": True, "watermark": "社外秘"}
            brand_id = fx.store.create_brand("試験のブランド", cfg)["id"]
        r = fx.request("POST", "/api/imports?runtime=cli&name=deck.pptx", raw_body=pptx_bytes(temp_dir(self)), session=True)
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        self.assertEqual(wait_until_idle(fx, deck, seconds=300)["state"], "done")
        view = fx.get(f"/api/decks/{deck}").json()
        self.assertIsNone(view["deck"]["brand"], "取り込んだ資料はブランドを出さない")

        def bare(run: dict) -> None:
            look = run["args"]["look"]
            self.assertEqual((look["logo"], look["footer"], look["page_numbers"], look["watermark"]), (None, "", False, ""))
            # 描いたもの（書き出す PPTX の中の XML の全部）にも、ブランドのフッタ・透かしの文字が入っていない
            import zipfile

            with zipfile.ZipFile(fx.root.run_dir(run["deck_id"], run["id"]) / "deck.pptx") as z:
                xml = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".xml"))
            self.assertIn("取り込み", xml, "ページの文字は入っている")
            self.assertNotIn("足されては困るフッタ", xml)
            self.assertNotIn("社外秘", xml)

        # 一覧: ブランドで絞ると、ブランドなしの資料は出ない（絞らなければ出る）
        self.assertIn(deck, [d["id"] for d in fx.get("/api/decks").json()["items"]])
        self.assertNotIn(deck, [d["id"] for d in fx.get(f"/api/decks?brand={brand_id}").json()["items"]])

        sid = view["pages"][0]["slide_id"]
        r = fx.post(f"/api/decks/{deck}/instructions", {"scope": "page", "slide_id": sid, "text": "見出しを言い切って"})
        self.assertEqual(r.status, 200, r.body)
        run = wait_until_idle(fx, deck, seconds=300)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertFalse(run["args"].get("imported"), "直しの回には取り込みの印が渡らない（デッキの印で見る）")
        bare(run)
        theme = (fx.root.run_settings_dir(deck, run["id"]) / "theme.yaml").read_text(encoding="utf-8")
        self.assertIn("取り込んだ PPTX のテーマ", theme, "直しの回は PPTX のテーマで組む")

        r = fx.post(f"/api/decks/{deck}/duplicate", {})
        self.assertEqual(r.status, 201, r.body)
        copy = r.json()["deck"]
        self.assertIsNone(copy["brand"], "複製もブランドなし")
        sid = fx.get(f"/api/decks/{copy['id']}").json()["pages"][0]["slide_id"]
        r = fx.post(f"/api/decks/{copy['id']}/instructions", {"scope": "page", "slide_id": sid, "text": "見出しを言い切って"})
        self.assertEqual(r.status, 200, r.body)
        run = wait_until_idle(fx, copy["id"], seconds=300)
        self.assertEqual(run["state"], "done", run["message"])
        bare(run)

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
