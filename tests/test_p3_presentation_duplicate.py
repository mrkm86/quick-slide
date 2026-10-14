"""P3: プレゼンの複製を本物のエンジンで検証する。"""

import json
import unittest
from pathlib import Path

import p3_edit_helpers as edit_helpers


class PresentationDuplicateTests(edit_helpers.PresentationEditBase):
    def test_duplicate_copies_pages_and_rebuild_draws_nothing(self) -> None:
        deck = self.make_deck()
        gens = len(self.gen_files())
        new = self.duplicate_and_rebuild(deck)
        self.assertEqual(len(self.gen_files()), gens * 2, "複製の区画に同じ絵が張られた（描いてはいない）")
        with self.fx.server.store_lock:
            ra, rb = self.fx.store.shown_run(deck), self.fx.store.shown_run(new)
        ca = json.loads((self.fx.root.run_dir(deck, ra["id"]) / "deck-info.json").read_text(encoding="utf-8"))["cache"]
        cb = json.loads((self.fx.root.run_dir(new, rb["id"]) / "deck-info.json").read_text(encoding="utf-8"))["cache"]
        self.assertNotEqual(ca, cb, "複製の回の記録は複製の区画を指す")
        a_files = {p.name: (p.stat().st_ino, p.read_bytes()) for p in Path(ca).glob("gen-*")}
        a_pics = {p.name: p.read_bytes() for p in (self.fx.root.run_dir(deck, ra["id"]) / "pictures").glob("*")}
        # 複製の 8 枚目の絵だけ直す → 複製の最初の版へ戻す。元のデッキの区画と控えは 1 バイトも変わらない
        self.instruct(new, [8], "絵だけ夜にして")
        self.assertEqual(self.wait(new)["state"], "done")
        p8 = next(p for p in self.view(new)["pages"] if p["key"] == "p-eight")
        r = self.fx.post(f"/api/decks/{new}/adopt", {"choices": {p8["slide_id"]: p8["versions"][-1]["id"]}})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.wait(new)["state"], "done")
        self.assertEqual({p.name: (p.stat().st_ino, p.read_bytes()) for p in Path(ca).glob("gen-*")}, a_files,
                         "複製の版を戻しても、元のデッキの区画は変わらない")
        self.assertEqual({p.name: p.read_bytes() for p in (self.fx.root.run_dir(deck, ra["id"]) / "pictures").glob("*")},
                         a_pics, "複製の直しで、元の回の控え（絵に添えた JSON を含む）は変わらない")
        for path in (f"/api/decks/{deck}/delete", f"/api/decks/{deck}/purge"):   # 元を完全に消しても複製は残る
            r = self.fx.post(path, {})
            self.assertEqual(r.status, 200, r.body)
        for p in self.view(new)["pages"]:
            self.assertEqual(self.fx.get(p["image_url"]).status, 200)
        self.assertEqual(len(self.gen_files()), gens, "元の区画だけが消え、複製の区画は残る")
        calls = self.codex_calls()
        self.assertEqual(self.fx.post(f"/api/decks/{new}/rebuild", {}).status, 200)
        self.assertEqual(self.wait(new)["state"], "done")
        self.assertEqual(self.drawn_pages(calls), [], "元を消したあとも、複製は描き直さない")


if __name__ == "__main__":
    unittest.main()
