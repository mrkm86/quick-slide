"""型のページ（Quick Slide で作ったページ）に、型の中身を変えずに絵・写真を重ねる（docs/adr/0039-add-pictures-to-made-pages.md）。

本物のエンジン（html-slide）と偽物の台本係で通す。空いている所は、描いた結果の部品（parts）から見る。
"""

from __future__ import annotations

import io
import unittest
import zipfile

import p3_edit_helpers as edit_helpers
from helpers import writer_calls

from quick_slide.render import illustrations as ILL


def png_bytes(size=(640, 480)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (30, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


class UnitTests(unittest.TestCase):
    def test_boxes_come_from_the_rendered_parts(self) -> None:
        slide = {"prims": [
            {"type": "rect", "rect": {"x": 0, "y": 0, "w": 1920, "h": 1080}},                      # ページ全体の地は除く
            {"type": "text", "rect": {"x": 120, "y": 146, "w": 960, "h": 80}, "runs": [{"text": "見出し"}, {"text": "の続き"}]},
            {"type": "img", "rect": {"x": 1200, "y": 300, "w": 600.4, "h": 400}},
            {"type": "rect", "rect": {"x": 120, "y": 306, "w": 528, "h": 518}},
            {"type": "path", "rect": None},                                                           # 位置の無い部品は除く
            {"type": "ellipse", "rect": {"x": 10, "y": 10, "w": 4, "h": 4}},                         # ごく小さい飾りは除く
        ]}
        self.assertEqual([(b["kind"], b["x"], b["y"], b["w"], b["h"], b["text"]) for b in ILL.part_boxes(slide)],
                         [("文字", 120, 146, 960, 80, "見出しの続き"), ("画像", 1200, 300, 600, 400, ""), ("図形", 120, 306, 528, 518, "")])
        self.assertEqual(ILL.part_boxes({}), [])
        # 全面の写真は箱に残す（その上は空いた所ではない）。ページ全体の地の図形だけを除く
        full = {"prims": [{"type": "img", "rect": {"x": 0, "y": 0, "w": 1920, "h": 1080}},
                          {"type": "rect", "rect": {"x": 0, "y": 0, "w": 1920, "h": 1080}},
                          {"type": "text", "rect": {"x": 120, "y": 800, "w": 900, "h": 80}, "runs": [{"text": "写真の上の文字"}]}]}
        self.assertEqual([(b["kind"], b["w"], b["h"]) for b in ILL.part_boxes(full)], [("画像", 1920, 1080), ("文字", 900, 80)])

    def test_overlays_survive_a_later_rewrite(self) -> None:
        """足したあとで台本係がページを直しても、重ねた絵は消えない（書き落としは引き継ぐ。外すのは空の配列を明示したとき）。"""
        from quick_slide.writer.patch import Patch, PagePatch, apply_revision

        item = {"x": 1200, "y": 400, "w": 400, "h": 300, "image": {"path": "overlays/a.png"}}
        script = {"slides": [{"id": "p1", "type": "bullets", "headline": "前", "items": ["a"], "overlays": [item]},
                             {"id": "p2", "type": "bullets", "headline": "ほか", "items": ["b"]}]}

        def revise(new_pages: list[dict]) -> list[dict]:
            patch = Patch()
            patch.pages["p1"] = PagePatch(key="p1", route="script", slides=new_pages)
            return apply_revision(script, patch)["slides"]

        kept = revise([{"id": "p1", "type": "bullets", "headline": "後", "items": ["a"]}])
        self.assertEqual((kept[0]["headline"], kept[0]["overlays"]), ("後", [item]), "書き落としても引き継ぐ")
        gone = revise([{"id": "p1", "type": "bullets", "headline": "後", "items": ["a"], "overlays": []}])
        self.assertNotIn("overlays", gone[0], "空の配列を明示したら外す")
        moved = {**item, "x": 100}
        self.assertEqual(revise([{"id": "p1", "type": "bullets", "headline": "後", "items": ["a"], "overlays": [moved]}])[0]["overlays"], [moved])
        split = revise([{"id": "p1", "type": "bullets", "headline": "後", "items": ["a"]},
                        {"id": "p1-b", "type": "bullets", "headline": "分けた", "items": ["c"]}])
        self.assertEqual((split[0]["overlays"], "overlays" in split[1]), ([item], False), "分けて増えたページには持ち越さない")
        self.assertNotIn("overlays", kept[1])


class EndToEndTests(edit_helpers.PresentationEditBase):
    def page_request(self, deck: str, position: int, **body) -> dict:
        sid = self.view(deck)["pages"][position - 1]["slide_id"]
        r = self.fx.post(f"/api/decks/{deck}/instructions", {"scope": "page", "slide_id": sid, "text": "", **body})
        self.assertEqual(r.status, 200, r.body)
        return r.json()

    def pictures(self, deck: str) -> list[list[str]]:
        from pptx import Presentation

        with self.fx.server.store_lock:
            run = self.fx.store.shown_run(deck)
        prs = Presentation(str(self.fx.root.run_dir(deck, run["id"]) / "deck.pptx"))
        return [[sh.name for sh in s.shapes if sh.shape_type == 13] for s in prs.slides]

    def test_an_illustration_and_a_picture_are_laid_over_made_pages(self) -> None:
        deck = self.make_deck()
        before_data, before_bytes = self.head(deck)
        before_pieces = self.page_bytes(before_bytes, before_data)
        before_pics = self.pictures(deck)

        # 3 枚目: 線のイラストを、空いた所に足す。型も中身もそのままで、overlays に 1 つ足される
        self.page_request(deck, 3, kind="illustration", options=["add"])
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertTrue(any(c.get("what") == "overlay" for c in writer_calls(self.log)), "重ねる頼みとして台本係に渡る")
        data, _ = self.head(deck)
        page, old = data["slides"][2], before_data["slides"][2]
        self.assertEqual({k: v for k, v in page.items() if k != "overlays"}, old, "型も中身も変わらない")
        self.assertEqual(len(page["overlays"]), 1)
        self.assertIn("<svg", page["overlays"][0]["image"]["svg"], "台本の版は SVG のまま持つ")
        self.assertEqual(self.view(deck)["pages"][2]["type"], old["type"])

        # 4 枚目: 手持ちの画像を、空いた所に足す（切り抜いて置く）
        up = self.fx.request("POST", "/api/settings/images", raw_body=png_bytes(), session=True)
        self.assertEqual(up.status, 201, up.body)
        self.page_request(deck, 4, kind="image", options=["add", "attached"],
                          attachments=[{"asset_id": up.json()["asset_id"], "input_method": "file"}])
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        data, raw = self.head(deck)
        item = data["slides"][3]["overlays"][0]
        self.assertTrue(item["image"]["path"].startswith("attachments/"))
        self.assertEqual(item["fit"], "cover")
        self.assertEqual({k: v for k, v in data["slides"][3].items() if k != "overlays"}, before_data["slides"][3])

        # ほかのページは 1 文字も変わらない。書き出す PPTX の 3・4 枚目に「重ねた絵」が入る
        after_pieces = self.page_bytes(raw, data)
        keys = {before_data["slides"][2]["id"], before_data["slides"][3]["id"]}
        for k in before_pieces:
            if k not in keys:
                self.assertEqual(before_pieces[k], after_pieces[k], k)
        pics = self.pictures(deck)
        self.assertEqual(pics[2].count("重ねた絵"), 1)
        self.assertEqual(pics[3].count("重ねた絵"), 1)
        self.assertEqual([p for i, p in enumerate(pics) if i not in (2, 3)], [p for i, p in enumerate(before_pics) if i not in (2, 3)])
        self.assert_screen_matches_pptx(deck)

        # 重ねた絵は「このページの記録」の本文・「自分で直す」の欄には出ない
        sid = self.view(deck)["pages"][2]["slide_id"]
        fields = self.fx.get(f"/api/decks/{deck}/pages/{sid}/texts").json()["fields"]
        self.assertFalse(any("overlays" in f["ref"] for f in fields))

    def test_a_deck_without_parts_is_redrawn_first(self) -> None:
        """見せている回に位置の記録（parts）が無い資料（同梱の見本・前に描いた資料）は、先に描き直してから足す。"""
        import shutil

        deck = self.make_deck()
        with self.fx.server.store_lock:
            first = self.fx.store.shown_run(deck)
        shutil.rmtree(self.fx.root.run_dir(deck, first["id"]) / "parts")
        before, _ = self.head(deck)
        self.page_request(deck, 3, kind="illustration", options=["add"])
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        with self.fx.server.store_lock:
            runs = [r for r in (self.fx.store.run(x["id"]) for x in self.fx.store._all(
                "SELECT id FROM runs WHERE deck_id=? ORDER BY number", (deck,))) if r["id"] != first["id"]]
        self.assertEqual([r["args"].get("edit") for r in runs], ["render", "revise"], "先に描き直し、そのあとで足す")
        with self.fx.server.store_lock:
            reasons = [self.fx.store.run_requests(deck)[r["id"]].get("reason") for r in runs]
        self.assertEqual(reasons[0], "parts")
        self.assertEqual([r["state"] for r in runs], ["done", "done"])
        data, _ = self.head(deck)
        self.assertEqual(len(data["slides"][2]["overlays"]), 1)
        self.assertEqual({k: v for k, v in data["slides"][2].items() if k != "overlays"}, before["slides"][2])

    def test_rebuild_still_goes_the_old_way(self) -> None:
        """「ページを組み直して入れる」は、今までどおり台本係がページを書き直す（重ねる頼みにはしない）。"""
        deck = self.make_deck()
        self.page_request(deck, 3, kind="illustration", options=["main"])
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        whats = [c.get("what") for c in writer_calls(self.log)]
        self.assertIn("revise", whats)
        self.assertNotIn("overlay", whats)
        data, _ = self.head(deck)
        self.assertNotIn("overlays", data["slides"][2])


if __name__ == "__main__":
    unittest.main()
