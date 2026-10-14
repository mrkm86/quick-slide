"""P3: ピッチの同時編集を本物のエンジンで検証する。"""

import time
import unittest

import p3_edit_helpers as edit_helpers


class PitchParallelTests(edit_helpers.EditBase):
    def test_other_pages_run_together_and_the_same_page_waits(self) -> None:
        """別のページの直しは同時に走り、両方の直しが残る。同じページへの 2 回目は 1 回目を待ち、その上に重なる
        （docs/adr/0012-parallel-edits.md）。"""
        deck = self.make_deck()
        self.tools.extra_env["FAKE_CODEX_SLEEP"] = "1.5"
        a = self.instruct(deck, [7], "見出しを短くして")
        b = self.instruct(deck, [2], "見出しを短くして")
        c = self.instruct(deck, [7], "見出しを短くして")
        self.assertFalse(b["queued"], "別のページの直しは待たない")
        self.assertTrue(c["queued"])
        self.assertIn("前の直しが終わってから", c["message"])
        deadline = time.monotonic() + 120
        seen_ab, seen_ac = False, False
        while time.monotonic() < deadline:
            with self.fx.server.store_lock:
                ja, jb, jc = (self.fx.store.job(x["job_id"]) for x in (a, b, c))
            seen_ab |= ja["state"] == "running" and jb["state"] == "running"
            seen_ac |= ja["state"] == "running" and jc["state"] == "running"
            if all(j["state"] in ("done", "failed") for j in (ja, jb, jc)):
                break
            time.sleep(0.05)
        self.assertTrue(seen_ab, "別のページの直しは同時に走る")
        self.assertFalse(seen_ac, "同じページの直しは 1 つずつ")
        self.assertEqual((ja["state"], jb["state"], jc["state"]), ("done", "done", "done"))
        with self.fx.server.store_lock:
            ra, rc = self.fx.store.run(a["run_id"]), self.fx.store.run(c["run_id"])
            evs = self.fx.store.events(deck_id=deck, kinds=("job_started", "job_finished"))
        order = [(e["kind"], e["payload"]["job_id"]) for e in evs if e["payload"]["job_id"] in (a["job_id"], c["job_id"])]
        self.assertEqual(order, [("job_started", a["job_id"]), ("job_finished", a["job_id"]),
                                 ("job_started", c["job_id"]), ("job_finished", c["job_id"])])
        data, _ = self.head(deck)
        self.assertTrue(data["slides"][6]["headline"].endswith("（直し）（直し）"), "同じページの 2 回目は 1 回目の上に重なる")
        self.assertTrue(data["slides"][1]["headline"].endswith("（直し）"), "別のページの直しも残る（上書きしない）")
        self.assertGreaterEqual(rc["record"]["base"]["version"], ra["record"]["edit"]["script_version"])
        self.assert_screen_matches_pptx(deck)
        pages = self.view(deck)["pages"]
        for n in (2, 7):
            notes = [v["revision_note"] for v in pages[n - 1]["versions"]]
            self.assertIn("見出しを短くして", notes, f"{n} 枚目の版に、そのページへの指示が付く")


if __name__ == "__main__":
    unittest.main()
