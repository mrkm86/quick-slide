"""P3: ピッチの編集を本物のエンジンで検証する。"""

import unittest

from helpers import writer_calls
import p3_edit_helpers as edit_helpers


class PitchEditTests(edit_helpers.EditBase):
    def test_revise_pages_7_and_8_redraws_only_them(self) -> None:
        deck = self.make_deck()
        before_data, before_bytes = self.head(deck)
        before_pieces = self.page_bytes(before_bytes, before_data)
        old = {p["key"]: p for p in self.view(deck)["pages"]}
        calls = self.codex_calls()
        r = self.instruct(deck, [7, 8], "見出しを短くして")
        self.assertIn("7・8ページ", r["message"])
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(self.drawn_pages(calls), [7, 8], "描き直すのは 7・8 枚目だけ（ほかはキャッシュの区画から）")
        after_data, after_bytes = self.head(deck)
        after_pieces = self.page_bytes(after_bytes, after_data)
        for key in before_pieces:
            if key in ("p-seven", "p-eight"):
                self.assertNotEqual(before_pieces[key], after_pieces[key])
            else:
                self.assertEqual(before_pieces[key], after_pieces[key], f"{key} は 1 文字も変わらない")
        self.assertEqual(after_data["slides"][6]["headline"], "立ったまま話すと短くなる（直し）")
        view = self.view(deck)
        for p in view["pages"]:
            if p["key"] in ("p-seven", "p-eight"):
                self.assertNotEqual(p["image_url"], old[p["key"]]["image_url"])
                self.assertEqual([v["version"] for v in p["versions"]], [2, 1])
                self.assertTrue(p["versions"][0]["active"])
                self.assertEqual(p["versions"][0]["revision_note"], "見出しを短くして")
            else:
                self.assertEqual(len(p["versions"]), 1, p["key"])
        self.assert_screen_matches_pptx(deck)
        # 提案（6 枚目へ）は当てずに、画面の帯と仕事の答えに出す
        job = self.fx.get(f"/api/decks/{deck}/jobs/{r['job_id']}").json()
        self.assertEqual([s["id"] for s in job["suggestions"]], ["p-rule"])
        self.assertEqual(job["routes"], {"p-seven": "script", "p-eight": "script"})
        self.assertIn("提案", view["planning"]["message"])
        self.assertEqual([p["title"] for p in job["pages"]][6], "立ったまま話すと短くなる（直し）")
        # 2 回目の回もエンジンは同じ作業フォルダに描き、回のフォルダに控える
        self.assertTrue((self.fx.root.run_dir(deck, run["id"]) / "deck.pptx").is_file())
        self.assertTrue((self.fx.root.run_dir(deck, run["id"]) / "pictures" / "p-seven.png").is_file())

    def test_second_pitch_run_with_one_changed_page_draws_only_that_page(self) -> None:
        """キャッシュの区画を回をまたいで使う（P2 は回ごとに新しい区画で、全ページを描き直していた）。"""
        deck = self.make_deck()
        calls = self.codex_calls()
        self.instruct(deck, [3], "見出しを変えて")
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(self.drawn_pages(calls), [3])

    def test_both_route_rewrites_then_revises(self) -> None:
        deck = self.make_deck()
        calls = self.codex_calls()
        self.instruct(deck, [7], "両方: 見出しを短くして絵も明るく")
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual([a[2] for a in run["record"]["argv"]], ["deck", "revise"])
        self.assertEqual(self.drawn_pages(calls), [7, 7], "台本の差し替えで 1 回、絵の直しで 1 回")
        self.assertEqual(self.head(deck)[0]["slides"][6]["headline"], "立ったまま話すと短くなる（直し）")
        self.assert_screen_matches_pptx(deck)

    def test_a_gone_page_fails_with_a_clear_message(self) -> None:
        deck = self.make_deck()
        pages = self.view(deck)["pages"]
        with self.fx.server.store_lock:
            res = self.fx.store.create_edit(deck, {"op": "revise", "pages": ["00000000-0000-4000-8000-000000000000"],
                                                   "text": "x", "source": "screen", "positions": [99]})
        self.fx.server.wake_worker()
        run = self.wait(deck)
        self.assertEqual((run["id"], run["state"]), (res["run"]["id"], "failed"))
        self.assertIn("もうありません", run["message"])
        self.assertEqual(len(self.view(deck)["pages"]), len(pages))

    def test_writer_answer_outside_the_targets_is_sent_back(self) -> None:
        self.tools.extra_env["FAKE_WRITER_MODE"] = "bad_revision_first"
        deck = self.make_deck()
        self.instruct(deck, [7], "見出しを短くして")
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        calls = [c for c in writer_calls(self.log) if c["what"] == "revise"]
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("直すページではない" in ln for ln in calls[1]["ng"]), calls[1]["ng"])


if __name__ == "__main__":
    unittest.main()
