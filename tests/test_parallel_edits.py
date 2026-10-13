"""同じデッキの仕事をどれだけ重ねるか（台帳の `claim_job`・`create_edit` の `ahead`。docs/adr/0012-parallel-edits.md）。

- 別のページを指定した直しどうしは同時に取る。同じページ・構成を変える操作・デッキ全体の直しは前を待つ
- 前から待っている仕事とぶつかるなら、あとから来た仕事は追い越さない
- 「前に待つ仕事の数」は、ぶつかる仕事だけを数える
台本係もエンジンも呼ばない（台帳だけ）。
"""

from __future__ import annotations

import unittest

from helpers import make_deck, open_store


def revise(*pages: str, scope: str = "page") -> dict:
    return {"op": "revise", "scope": scope, "pages": list(pages), "text": "見出しを短く", "source": "screen"}


class ClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = open_store(self)
        self.deck = make_deck(self.store)["id"]

    def edit(self, request: dict) -> dict:
        return self.store.create_edit(self.deck, request)

    def claim(self) -> str | None:
        job = self.store.claim_job("w")
        return None if job is None else job["id"]

    def test_other_pages_overlap_and_the_same_page_waits(self) -> None:
        a = self.edit(revise("p1"))
        b = self.edit(revise("p2"))
        c = self.edit(revise("p1", "p3", scope="pages"))
        d = self.edit(revise("p4"))
        self.assertEqual((a["ahead"], b["ahead"], c["ahead"], d["ahead"]), (0, 0, 1, 0), "ぶつかる仕事だけを数える")
        self.assertEqual(self.claim(), a["job"]["id"])
        self.assertEqual(self.claim(), b["job"]["id"], "別のページは同時に取る")
        self.assertEqual(self.claim(), d["job"]["id"], "p1 を待つ直しは飛ばし、ぶつからない直しを取る")
        self.assertIsNone(self.claim())
        self.store.finish_job(a["job"]["id"], "done")
        self.assertEqual(self.claim(), c["job"]["id"], "p1 の直しが終わったら取る")

    def test_structural_operations_and_whole_deck_edits_do_not_overlap(self) -> None:
        a = self.edit(revise("p1"))
        b = self.edit({"op": "insert", "after": None, "text": "足す", "source": "screen"})
        c = self.edit(revise("p5"))
        whole = {"op": "revise", "scope": "deck", "pages": ["p1", "p2"], "text": "全体", "source": "screen"}
        self.assertEqual((b["ahead"], c["ahead"]), (1, 1), "足す仕事の後ろの直しは、足す仕事を待つ")
        self.assertEqual(self.claim(), a["job"]["id"])
        self.assertIsNone(self.claim(), "足す仕事は走っている直しを待つ。その後ろの直しも追い越さない")
        self.store.finish_job(a["job"]["id"], "done")
        self.assertEqual(self.claim(), b["job"]["id"])
        self.assertIsNone(self.claim())
        self.store.finish_job(b["job"]["id"], "done")
        self.assertEqual(self.claim(), c["job"]["id"])
        e = self.edit(whole)
        self.assertEqual(e["ahead"], 1)
        self.assertIsNone(self.claim(), "デッキ全体の直しは重ねない")

    def test_other_decks_do_not_wait(self) -> None:
        other = make_deck(self.store, "別のスライド")["id"]
        a = self.edit(revise("p1"))
        b = self.store.create_edit(other, revise("p1"))
        self.assertEqual(b["ahead"], 0)
        self.assertEqual({self.claim(), self.claim()}, {a["job"]["id"], b["job"]["id"]})


if __name__ == "__main__":
    unittest.main()
