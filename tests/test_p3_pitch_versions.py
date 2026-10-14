"""P3: ピッチの版の復元を本物のエンジンで検証する。"""

import json
import unittest

import p3_edit_helpers as edit_helpers
from quick_slide.scriptdoc import dumps, without_pins


class PitchVersionTests(edit_helpers.EditBase):
    def test_image_only_route_uses_engine_revise(self) -> None:
        deck = self.make_deck()
        before_data, before_bytes = self.head(deck)
        old = {p["key"]: p for p in self.view(deck)["pages"]}
        calls = self.codex_calls()
        r = self.instruct(deck, [8], "絵だけ夜の景色にして", edit_mode="minor")
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(self.drawn_pages(calls), [8])
        self.assertEqual(self.head(deck)[1], before_bytes, "絵だけの直しは台本を変えない")
        argv = run["record"]["argv"]
        self.assertEqual([a[2] for a in argv], ["revise"], "deck を流さず revise だけ")
        self.assertIn("--page", argv[0])
        self.assertEqual(argv[0][argv[0].index("--page") + 1], "8")
        self.assertNotIn("--remake", argv[0])
        # 微修正: 今の絵を土台に渡した（参照画像が 1 枚）
        rec = [json.loads((self.log / "codex-calls" / n).read_text(encoding="utf-8")) for n in sorted(self.codex_calls() - calls)]
        self.assertEqual([len(x["refs"]) for x in rec if x.get("cmd") == "exec" and "t1" in x], [1])
        p8 = next(p for p in self.view(deck)["pages"] if p["key"] == "p-eight")
        self.assertNotEqual(p8["image_url"], old["p-eight"]["image_url"])
        self.assertEqual([v["version"] for v in p8["versions"]], [2, 1])
        job = self.fx.get(f"/api/decks/{deck}/jobs/{r['job_id']}").json()
        self.assertEqual(job["routes"], {"p-eight": "image"})
        self.assert_screen_matches_pptx(deck)
        # 版から戻す: 絵だけが違ったので、前の絵を区画へ戻し、描かずに組み直す
        calls = self.codex_calls()
        older = p8["versions"][1]
        r = self.fx.post(f"/api/decks/{deck}/adopt", {"choices": {p8["slide_id"]: older["id"]}})
        self.assertEqual(r.status, 200, r.body)
        self.assertTrue(r.json()["started"])
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(self.drawn_pages(calls), [], "戻すときは描かない")
        p8 = next(p for p in self.view(deck)["pages"] if p["key"] == "p-eight")
        self.assertEqual(p8["versions"][0]["version"], 1)
        self.assertTrue(p8["versions"][0]["active"], "戻した姿が使用中")
        self.assertEqual(len(p8["versions"]), 2, "同じ姿は 1 つにまとめる")
        self.assertEqual(dumps(without_pins(self.head(deck)[0])), before_bytes, "台本は（版の印を除いて）元のまま")
        with self.fx.server.store_lock:
            rows = {r["key"]: r for r in self.fx.store.run_pages(run["id"])}
            first = {r["key"]: r for r in self.fx.store.run_pages(self.fx.store.runs_for_deck(deck)[-1]["id"])}
        self.assertEqual(rows["p-eight"]["picture_sha256"], first["p-eight"]["picture_sha256"])
        self.assert_screen_matches_pptx(deck)
        # もう使用中の版を押しても何も作らない
        r = self.fx.post(f"/api/decks/{deck}/adopt", {"choices": {p8["slide_id"]: p8["versions"][0]["id"]}})
        self.assertEqual((r.status, r.json()["started"]), (200, False))

    def test_restore_script_version_draws_nothing(self) -> None:
        deck = self.make_deck()
        _, first_bytes = self.head(deck)
        self.instruct(deck, [7], "見出しを短くして")
        self.assertEqual(self.wait(deck)["state"], "done")
        p7 = next(p for p in self.view(deck)["pages"] if p["key"] == "p-seven")
        calls = self.codex_calls()
        r = self.fx.post(f"/api/decks/{deck}/adopt", {"choices": {p7["slide_id"]: p7["versions"][1]["id"]}})
        self.assertEqual(r.status, 200, r.body)
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(self.drawn_pages(calls), [], "前の絵を区画へ戻すので描かない")
        data, _ = self.head(deck)
        self.assertEqual(next(s for s in data["slides"] if s["id"] == "p-seven").get("_pin"), p7["versions"][1]["id"],
                         "戻したページは、その版を描いた回を指す")
        self.assertEqual(dumps(without_pins(data)), first_bytes, "台本も（版の印を除いて）1 冊目と同じ姿に戻る")
        p7 = next(p for p in self.view(deck)["pages"] if p["key"] == "p-seven")
        self.assertEqual((p7["version"], p7["title"]), (1, "立ったまま話すと短くなる"))
        self.assert_screen_matches_pptx(deck)

    def legacy_first_run(self, deck: str) -> dict:
        """1 回目の回を P2（移行 0003 の前）の形にする: ページの中身と絵の sha256 が無い。"""
        with self.fx.server.store_lock:
            first = self.fx.store.runs_for_deck(deck)[-1]
            self.fx.store.conn.execute("UPDATE run_pages SET content_sha256=NULL,picture_sha256=NULL WHERE run_id=?",
                                       (first["id"],))
            self.fx.store.conn.execute("UPDATE script_pages SET content_sha256=NULL WHERE script_id=?",
                                       (first["script_id"],))
        return first

    def check_legacy_versions(self, deck: str) -> None:
        for p in self.view(deck)["pages"]:
            if p["key"] in ("p-why", "p-agenda"):
                self.assertEqual([v["version"] for v in p["versions"]], [2, 1], p["key"])
                self.assertEqual([v["revision_note"] for v in p["versions"]], ["見出しを短くして", None],
                                 "指示の文は、その指示が向いたページの版にだけ付く")
            else:
                self.assertEqual([(v["version"], v["active"], v["revision_note"]) for v in p["versions"]],
                                 [(1, True, None)], f"{p['key']}: 直していないページは版が 1 つ・指示も付かない")

    def test_legacy_run_without_shas_does_not_add_versions(self) -> None:
        deck = self.make_deck()
        first = self.legacy_first_run(deck)
        self.instruct(deck, [2, 4], "見出しを短くして")
        self.assertEqual(self.wait(deck)["state"], "done")
        self.check_legacy_versions(deck)
        # その回の台本の版のファイルが読めなくても、画像のバイトが同じなら同じ姿とみなす
        from quick_slide.server.page_view import LegacyContent, resolve_states

        with self.fx.server.store_lock:
            path = self.fx.store.script_path(self.fx.store.script(first["script_id"]))
        path.rename(path.with_suffix(".gone"))
        LegacyContent._memo.clear()
        self.check_legacy_versions(deck)
        rows = [{"asset_sha256": "x", "content_sha256": "c2", "picture_sha256": "p2"},
                {"asset_sha256": "x", "content_sha256": None, "picture_sha256": None},
                {"asset_sha256": "y", "content_sha256": None, "picture_sha256": None}]
        self.assertEqual(resolve_states(rows, lambda r: None), [("c2", "p2"), ("c2", "p2"), ("asset:y", "asset:y")])
        self.assertEqual(resolve_states(rows, lambda r: "c-old"), [("c2", "p2"), ("c-old", "p2"), ("c-old", "asset:y")])


if __name__ == "__main__":
    unittest.main()
