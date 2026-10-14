"""P3: プレゼンの編集を本物のエンジンで検証する。"""

import json
import unittest

import p3_edit_helpers as edit_helpers


class PresentationEditTests(edit_helpers.PresentationEditBase):
    def test_other_pages_run_together_in_a_presentation(self) -> None:
        """プレゼンでも、別のページの直しは同時に走り、両方が残る（検査は回ごとの台本ファイル。docs/adr/0012）。"""
        deck = self.make_deck()
        a = self.instruct(deck, [4], "見出しを短くして")
        b = self.instruct(deck, [6], "見出しを短くして")
        self.assertFalse(b["queued"])
        run = self.wait(deck)
        with self.fx.server.store_lock:
            states = [self.fx.store.job(x["job_id"])["state"] for x in (a, b)]
            checks = list(self.fx.root.scripts_dir(deck).glob("check-*.json"))
        self.assertEqual(states, ["done", "done"], run["message"])
        self.assertEqual(checks, [], "検査の台本ファイルは残さない")
        data, _ = self.head(deck)
        self.assertTrue(data["slides"][3]["headline"].endswith("（直し）"))
        self.assertTrue(data["slides"][5]["headline"].endswith("（直し）"))
        self.assert_screen_matches_pptx(deck)

    def test_revise_7_and_8_keeps_other_slots_and_restore(self) -> None:
        deck = self.make_deck()
        before_data, before_bytes = self.head(deck)
        before_pieces = self.page_bytes(before_bytes, before_data)
        gens = self.gen_files()
        self.assertEqual(len(gens), 4, "画像の枠は 1・5・7・8 枚目")
        calls = self.codex_calls()
        self.instruct(deck, [7, 8], "絵も変えて 見出しを短くして")
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        after_data, after_bytes = self.head(deck)
        after_pieces = self.page_bytes(after_bytes, after_data)
        for key in before_pieces:
            if key not in ("p-seven", "p-eight"):
                self.assertEqual(before_pieces[key], after_pieces[key], key)
        now = self.gen_files()
        kept = {k: v for k, v in gens.items() if k in now and now[k] == v}
        self.assertEqual(len(kept), 4, "前の枠の絵はそのまま（同じ inode）")
        self.assertEqual(len(now) - len(gens), 2, "描いたのは 7・8 枚目の枠だけ")
        execs = [n for n in self.codex_calls() - calls
                 if (lambda r: r.get("cmd") == "exec" and "t1" in r)(json.loads((self.log / "codex-calls" / n).read_text(encoding="utf-8")))]
        self.assertEqual(len(execs), 2)
        self.assert_screen_matches_pptx(deck)
        # 絵だけの直し（revise）→ 戻す
        p8 = next(p for p in self.view(deck)["pages"] if p["key"] == "p-eight")
        self.assertTrue(p8["can_minor"]["available"])
        self.instruct(deck, [8], "絵だけ夜にして")
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual([a[2] for a in run["record"]["argv"]], ["revise"])
        p8 = next(p for p in self.view(deck)["pages"] if p["key"] == "p-eight")
        self.assertEqual([v["version"] for v in p8["versions"]], [3, 2, 1])
        calls = self.codex_calls()
        r = self.fx.post(f"/api/decks/{deck}/adopt", {"choices": {p8["slide_id"]: p8["versions"][1]["id"]}})
        self.assertEqual(r.status, 200, r.body)
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(self.codex_calls() - calls, set(), "戻すときは描かない")
        p8 = next(p for p in self.view(deck)["pages"] if p["key"] == "p-eight")
        self.assertEqual((p8["versions"][0]["version"], p8["versions"][0]["active"]), (2, True))
        self.assert_screen_matches_pptx(deck)


if __name__ == "__main__":
    unittest.main()
