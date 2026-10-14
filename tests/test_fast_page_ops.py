"""描かない直し（docs/EDIT-PLAN.md の A）: 非表示・並べ替えは、仕事を待たずにその場で台本に当たる。

- 答えが返った時点で、画面のページ（並び・非表示）と発表の並びがもう新しい
- 当てた後に、発表用の HTML と書き出しを揃える組み直し（`render` の `reason: tidy`）が 1 つ入る。その間、帯は出さない
- 組み直しが終わる前に書き出しても、隠したページは入らない（古い回のファイルを配らない）
- 直しの仕事が走っている間は、これまでどおり仕事の列に入れる（割り込むと、走っている直しが当てた直しを消す）
"""

from __future__ import annotations

import time
import unittest

from p3_edit_helpers import EditBase, pptx_notes


class FastPageOpsTests(EditBase):
    mode = "pitch"

    def open_jobs(self, deck: str) -> list[dict]:
        with self.fx.server.store_lock:
            return self.fx.store.open_jobs(deck)

    def test_hide_and_reorder_apply_at_once(self) -> None:
        deck = self.make_deck()
        pages = self.view(deck)["pages"]
        target = pages[1]
        r = self.fx.post(f"/api/decks/{deck}/pages/{target['slide_id']}/visibility", {"hidden": True})
        self.assertEqual(r.status, 200, r.body)
        body = r.json()
        self.assertTrue(body["applied"])
        self.assertIn("非表示にしました", body["message"])
        # 答えが返った時点で、台本と画面はもう新しい（仕事を待たない）
        data, _ = self.head(deck)
        self.assertTrue(next(s for s in data["slides"] if s["id"] == target["key"]).get("_hidden"))
        view = self.view(deck)
        self.assertTrue(next(p for p in view["pages"] if p["slide_id"] == target["slide_id"])["hidden"])
        self.assertFalse(any(p["display"]["key"] in ("generating", "editing") for p in view["pages"]),
                         "描かない直しで「生成中」「編集中」を出さない")
        if view["live"]["working"]:
            self.assertFalse(view["planning"]["active"], "裏の組み直しの間は帯を出さない")
        present = self.fx.get(f"/api/decks/{deck}/present").json()
        self.assertNotIn(target["slide_id"], [p["slide_id"] for p in present["pages"]], "発表は隠したページを飛ばす")
        # 並べ替えもその場で
        ids = [p["slide_id"] for p in view["pages"]]
        ids = [ids[-1]] + ids[:-1]
        r = self.fx.post(f"/api/decks/{deck}/pages/reorder", {"slide_ids": ids})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual([p["slide_id"] for p in self.view(deck)["pages"]], ids)
        present = self.fx.get(f"/api/decks/{deck}/present").json()
        self.assertEqual([p["slide_id"] for p in present["pages"]], [i for i in ids if i != target["slide_id"]],
                         "発表は新しい並びに従う（組み直しの前から）")
        # 組み直しの仕事は、待っているものがあれば足さない（多くても走っている 1 つと待っている 1 つ）
        tidy = [j for j in self.open_jobs(deck) if (j["request"] or {}).get("reason") == "tidy"]
        self.assertLessEqual(len([j for j in tidy if j["state"] == "queued"]), 1)
        self.assertEqual(self.wait(deck)["state"], "done")
        self.assert_screen_matches_pptx(deck)

    def test_show_again_is_ready_at_once(self) -> None:
        deck = self.make_deck()
        target = self.view(deck)["pages"][2]
        self.fx.post(f"/api/decks/{deck}/pages/{target['slide_id']}/visibility", {"hidden": True})
        self.assertEqual(self.wait(deck)["state"], "done")
        r = self.fx.post(f"/api/decks/{deck}/pages/{target['slide_id']}/visibility", {"hidden": False})
        self.assertEqual(r.status, 200, r.body)
        page = next(p for p in self.view(deck)["pages"] if p["slide_id"] == target["slide_id"])
        self.assertFalse(page["hidden"])
        self.assertEqual(page["display"]["key"], "ready", "前の回で描いた絵のまま、すぐに出す")
        self.assertTrue(page["image_url"])
        present = self.fx.get(f"/api/decks/{deck}/present").json()
        self.assertIn(target["slide_id"], [p["slide_id"] for p in present["pages"]])
        self.assertEqual(self.wait(deck)["state"], "done")
        self.assert_screen_matches_pptx(deck)

    def test_export_right_after_hide_leaves_the_page_out(self) -> None:
        deck = self.make_deck()
        target = self.view(deck)["pages"][1]
        self.fx.post(f"/api/decks/{deck}/pages/{target['slide_id']}/visibility", {"hidden": True})
        r = self.fx.post(f"/api/decks/{deck}/exports", {"format": "pptx"})
        self.assertEqual(r.status, 201, r.body)
        with self.fx.server.store_lock:
            rec = self.fx.store.export(r.json()["export"]["id"])
        notes = pptx_notes(self.fx.root.root / rec["path"])
        visible = [p for p in self.view(deck)["pages"] if not p["hidden"]]
        self.assertEqual(len(notes), len(visible), "組み直しの前に書き出しても、隠したページは入らない")

    def test_hide_while_an_edit_runs_is_queued_and_kept(self) -> None:
        deck = self.make_deck()
        self.tools.extra_env["FAKE_CODEX_SLEEP"] = "1.5"
        self.instruct(deck, [7], "見出しを短くして")
        target = self.view(deck)["pages"][1]
        r = self.fx.post(f"/api/decks/{deck}/pages/{target['slide_id']}/visibility", {"hidden": True})
        self.assertEqual(r.status, 200, r.body)
        self.assertNotIn("applied", r.json(), "直しが走っている間は仕事にする")
        self.assertTrue(r.json()["queued"])
        deadline = time.monotonic() + 300
        while self.open_jobs(deck) and time.monotonic() < deadline:
            time.sleep(0.2)
        data, _ = self.head(deck)
        self.assertTrue(next(s for s in data["slides"] if s["id"] == target["key"]).get("_hidden"),
                        "走っていた直しが終わっても、非表示は消えない")
        self.assert_screen_matches_pptx(deck)


if __name__ == "__main__":
    unittest.main()
