"""P3: ピッチのページ操作を本物のエンジンで検証する。"""

import unittest

from helpers import writer_calls
import p3_edit_helpers as edit_helpers


class PitchPageTests(edit_helpers.EditBase):
    def test_insert_duplicate_hide_and_reorder(self) -> None:
        deck = self.make_deck()
        pages = self.view(deck)["pages"]
        # 差し込み（台本係が 1 ページ書く）: 足したページだけ描く
        calls = self.codex_calls()
        r = self.fx.post(f"/api/decks/{deck}/pages/insert", {"after_slide_id": pages[1]["slide_id"], "text": "費用の内訳が欲しい"})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.view(deck)["pending_inserts"][0]["after_slide_id"], pages[1]["slide_id"])
        self.assertEqual(self.wait(deck)["state"], "done")
        self.assertEqual(self.drawn_pages(calls), [3], "足したページ（3 枚目）だけ描く。後ろのページは区画で付け替える")
        keys = [p["key"] for p in self.view(deck)["pages"]]
        self.assertEqual(keys[:4], ["cover", "p-why", "p-added-1", "p-sec"])
        self.assertEqual(writer_calls(self.log)[-1]["instruction"], "費用の内訳が欲しい")
        self.assert_screen_matches_pptx(deck)
        # 複製: 同じ中身なので描かない。複製したページの絵は元と同じバイトだが、どのページも自分の絵の URL を保つ
        calls = self.codex_calls()
        urls = {p["key"]: p["image_url"] for p in self.view(deck)["pages"]}
        p5 = self.view(deck)["pages"][4]
        r = self.fx.post(f"/api/decks/{deck}/pages/{p5['slide_id']}/duplicate", {})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.wait(deck)["state"], "done")
        self.assertEqual(self.drawn_pages(calls), [])
        keys = [p["key"] for p in self.view(deck)["pages"]]
        self.assertEqual(keys[4:6], ["p-agenda", "p-agenda-copy"])
        now = {p["key"]: p["image_url"] for p in self.view(deck)["pages"]}
        self.assertEqual({k: now[k] for k in urls}, urls, "直していないページの絵の URL は変わらない")
        self.assertNotEqual(now["p-agenda-copy"], now["p-agenda"], "複製は自分の資産")
        self.assert_screen_matches_pptx(deck)
        urls = now
        # 非表示: エンジンには渡さず、画面には残る（灰色）。描かない
        calls = self.codex_calls()
        target = self.view(deck)["pages"][1]
        r = self.fx.post(f"/api/decks/{deck}/pages/{target['slide_id']}/visibility", {"hidden": True})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.wait(deck)["state"], "done")
        self.assertEqual(self.drawn_pages(calls), [])
        view = self.view(deck)
        hidden = [p for p in view["pages"] if p["hidden"]]
        self.assertEqual([p["key"] for p in hidden], ["p-why"])
        self.assertEqual({p["key"]: p["image_url"] for p in view["pages"]}, urls, "並びがずれても絵の URL は変わらない")
        self.assertTrue(hidden[0]["image_url"], "隠したページも前の絵で出す")
        self.assertEqual(view["counts"]["total"], 11)
        self.assert_screen_matches_pptx(deck)
        # 並べ替え: 描かない。答えは新しい並び
        calls = self.codex_calls()
        ids = [p["slide_id"] for p in view["pages"]]
        ids = [ids[-1]] + ids[:-1]
        r = self.fx.post(f"/api/decks/{deck}/pages/reorder", {"slide_ids": ids})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual([p["slide_id"] for p in r.json()["pages"]], ids)
        self.assertEqual(self.wait(deck)["state"], "done")
        self.assertEqual(self.drawn_pages(calls), [])
        self.assertEqual([p["slide_id"] for p in self.view(deck)["pages"]], ids)
        self.assert_screen_matches_pptx(deck)
        # 表示に戻す: 前の絵（控え）を区画に戻すので描かない
        calls = self.codex_calls()
        r = self.fx.post(f"/api/decks/{deck}/pages/{target['slide_id']}/visibility", {"hidden": False})
        self.assertEqual(self.wait(deck)["state"], "done")
        self.assertEqual(self.drawn_pages(calls), [])
        self.assert_screen_matches_pptx(deck)

    def test_split_page_inserts_new_ids_in_place(self) -> None:
        deck = self.make_deck()
        calls = self.codex_calls()
        self.instruct(deck, [7], "2枚に分けて")
        self.assertEqual(self.wait(deck)["state"], "done")
        keys = [p["key"] for p in self.view(deck)["pages"]]
        self.assertEqual(keys[6:8], ["p-seven", "p-seven-b"])
        self.assertEqual(self.drawn_pages(calls), [7, 8], "分けた 2 ページだけ描く（後ろのページは付け替え）")
        self.assert_screen_matches_pptx(deck)


if __name__ == "__main__":
    unittest.main()
