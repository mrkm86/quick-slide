"""ページの文字を人が直接書き換える（docs/adr/0036-direct-text-edits.md）。台本係は通らない。

- 欄の取り出しと当て方（`pagetext`）は、台本だけで見る
- 口と仕事は本物のエンジン（html-slide）で通す。Node と Chrome が無ければ skip
"""

from __future__ import annotations

import unittest

import p3_edit_helpers as edit_helpers
from helpers import fake_tools, start_server, temp_dir, wait_until_idle, writer_calls

from quick_slide import pagetext
from quick_slide.settings import Settings, WorkerSettings

from test_engines_e2e import html_slide_ready
from test_import_pptx import pptx_bytes

IMPORTED = {"id": "p-001", "type": "html", "title": "見出し",
            "html": '<div><p style="left:0px">見出し</p><p>•</p><p> 一つ目 &amp; 二つ目 </p></div>'}
FREE = {"id": "p-free", "type": "free", "kicker": "課題", "headline": "歩行が 55% を占める", "takeaway_icon": "alert",
        "body": '<div class="f-box"><span class="f-icon" data-icon="clock"></span><div class="f-num">55</div><div>%</div></div>',
        "talk": ["話すこと"], "image": {"prompt": "倉庫", "alt": "倉庫の絵"}}
TYPED = {"id": "closing", "type": "closing", "headline": "次回の役員会で決めたい", "layout": "graphic",
         "items": [{"title": "着手を決める", "body": "4,500 万円", "icon": "check"}], "_hidden": True}


class UnitTests(unittest.TestCase):
    def refs(self, page: dict) -> dict[str, str]:
        return {f["ref"]: f["text"] for f in pagetext.fields(page)}

    def test_fields_are_only_the_visible_texts(self) -> None:
        self.assertEqual(self.refs(IMPORTED), {"n:1": "見出し", "n:3": "一つ目 & 二つ目"}, "箇条書きの印は欄にしない")
        self.assertEqual(self.refs(FREE), {"k:kicker": "課題", "k:headline": "歩行が 55% を占める", "b:1": "55", "b:2": "%"},
                         "アイコンの名前・話す台本・画像の指示は欄にしない")
        self.assertEqual(self.refs(TYPED), {"k:headline": "次回の役員会で決めたい", "k:items/0/title": "着手を決める",
                                           "k:items/0/body": "4,500 万円"})
        labels = [f["label"] for f in pagetext.fields(FREE)]
        self.assertEqual(labels, ["小見出し", "見出し", "本文 1", "本文 2"], "同じ見出しの欄には番号を振る")

    def test_drawing_choices_are_not_fields(self) -> None:
        """描き方の指定（図柄・札の描き方・進み具合・並べ方）は、文字列でも欄にしない。知らないキーも欄にしない。"""
        cover = {"id": "c", "type": "cover", "title": "表紙", "label": "提案", "motif": "wave", "layout": "graphic", "tone": "dark"}
        self.assertEqual(self.refs(cover), {"k:title": "表紙", "k:label": "提案"})
        notice = {"id": "n", "type": "notice", "headline": "注意", "label_style": "tag", "unknown_key": "見えない値",
                  "items": [{"label": "重要", "body": "止める", "state": "done", "icon": "alert"}, "文字だけの項目"]}
        self.assertEqual(self.refs(notice), {"k:headline": "注意", "k:items/0/label": "重要", "k:items/0/body": "止める",
                                            "k:items/1": "文字だけの項目"})

    def test_the_list_title_follows_only_the_heading_segment(self) -> None:
        """本文に見出しと同じ文字があっても、本文の書き換えでは一覧の見出し（title）を変えない。"""
        page = {"id": "p", "type": "html", "title": "同じ文字", "html": "<p>同じ文字</p><p>同じ文字</p>"}
        body = pagetext.apply(page, [{"ref": "n:2", "from": "同じ文字", "text": "本文だけ"}])
        self.assertEqual((body["title"], body["html"]), ("同じ文字", "<p>同じ文字</p><p>本文だけ</p>"))
        head = pagetext.apply(page, [{"ref": "n:1", "from": "同じ文字", "text": "新しい見出し"}])
        self.assertEqual((head["title"], head["html"]), ("新しい見出し", "<p>新しい見出し</p><p>同じ文字</p>"))

    def test_apply_changes_only_the_named_texts(self) -> None:
        got = pagetext.apply(IMPORTED, [{"ref": "n:3", "from": "一つ目 & 二つ目", "text": "A < B"},
                                        {"ref": "n:1", "from": "見出し", "text": "新しい見出し"}])
        self.assertEqual(got["html"], '<div><p style="left:0px">新しい見出し</p><p>•</p><p> A &lt; B </p></div>')
        self.assertEqual(got["title"], "新しい見出し", "一覧に出す見出しも直す")
        self.assertEqual(IMPORTED["title"], "見出し", "元のページは変えない")
        got = pagetext.apply(FREE, [{"ref": "b:1", "from": "55", "text": "60"}, {"ref": "k:headline", "from": "歩行が 55% を占める",
                                                                                  "text": "歩行が 60% を占める"}])
        self.assertIn('<div class="f-num">60</div>', got["body"])
        self.assertEqual(got["headline"], "歩行が 60% を占める")
        self.assertEqual({k: v for k, v in got.items() if k not in ("body", "headline")},
                         {k: v for k, v in FREE.items() if k not in ("body", "headline")})
        got = pagetext.apply(TYPED, [{"ref": "k:items/0/body", "from": "4,500 万円", "text": "800 万円"}])
        self.assertEqual(got["items"], [{"title": "着手を決める", "body": "800 万円", "icon": "check"}])

    def test_a_text_that_moved_is_refused(self) -> None:
        for change in ({"ref": "n:3", "from": "古い文字", "text": "x"}, {"ref": "n:9", "from": "見出し", "text": "x"},
                       {"ref": "k:layout", "from": "graphic", "text": "x"}):
            with self.assertRaises(pagetext.TextEditRefused):
                pagetext.apply(IMPORTED if change["ref"][0] == "n" else TYPED, [change])

    def test_changes_are_checked_and_unchanged_ones_dropped(self) -> None:
        ok = pagetext.check_changes([{"ref": "n:1", "from": "見出し", "text": " 新しい\n見出し "},
                                     {"ref": "n:3", "from": "同じ", "text": "同じ"}])
        self.assertEqual(ok, [{"ref": "n:1", "from": "見出し", "text": "新しい 見出し"}])
        for bad in (None, [], [{"ref": "n:1", "from": "a", "text": " "}], [{"ref": "x:1", "from": "a", "text": "b"}],
                    [{"ref": "n:1", "from": "a", "text": "a"}], [{"ref": "n:1", "from": "a", "text": "あ" * 2001}],
                    [{"ref": "n:1", "from": "a", "text": "b"}, {"ref": "n:1", "from": "a", "text": "c"}]):
            with self.assertRaises(pagetext.TextEditRefused, msg=repr(bad)[:60]):
                pagetext.check_changes(bad)


class ImportedEndToEndTests(unittest.TestCase):
    def test_a_person_rewrites_texts_of_an_imported_page_without_the_writer(self) -> None:
        why = html_slide_ready()
        if why:
            self.skipTest(why)
        tools, log = fake_tools(self)
        fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        r = fx.request("POST", "/api/imports?runtime=cli&name=deck.pptx", raw_body=pptx_bytes(temp_dir(self)), session=True)
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        self.assertEqual(wait_until_idle(fx, deck, seconds=300)["state"], "done")
        page = fx.get(f"/api/decks/{deck}").json()["pages"][0]
        sid = page["slide_id"]
        got = fx.get(f"/api/decks/{deck}/pages/{sid}/texts").json()
        self.assertTrue(got["editable"], got)
        by_text = {f["text"]: f["ref"] for f in got["fields"]}
        self.assertIn("取り込みの見出し", by_text)
        before = len(writer_calls(log))

        # 元の文字が違う書き換えは当てない
        r = fx.post(f"/api/decks/{deck}/pages/{sid}/texts",
                    {"changes": [{"ref": by_text["取り込みの見出し"], "from": "別の文字", "text": "x"}]})
        self.assertEqual((r.status, r.json()["error"]["code"]), (409, "text_changed"), r.body)
        r = fx.post(f"/api/decks/{deck}/pages/{sid}/texts",
                    {"changes": [{"ref": by_text["取り込みの見出し"], "from": "取り込みの見出し", "text": "書き換えた見出し"}]})
        self.assertEqual(r.status, 200, r.body)
        run = wait_until_idle(fx, deck, seconds=300)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(len(writer_calls(log)), before, "台本係は呼ばない")
        self.assertEqual(run["record"]["edit"]["op"], "retext")
        page = fx.get(f"/api/decks/{deck}").json()["pages"][0]
        self.assertEqual((page["title"], page["version"]), ("書き換えた見出し", 2))
        texts = [f["text"] for f in fx.get(f"/api/decks/{deck}/pages/{sid}/texts").json()["fields"]]
        self.assertIn("書き換えた見出し", texts)
        self.assertNotIn("取り込みの見出し", texts)


class MadeDeckTests(edit_helpers.PresentationEditBase):
    def test_a_person_rewrites_a_headline_of_a_made_page(self) -> None:
        deck = self.make_deck()
        before_data, before_bytes = self.head(deck)
        before_pieces = self.page_bytes(before_bytes, before_data)
        page = self.view(deck)["pages"][3]
        got = self.fx.get(f"/api/decks/{deck}/pages/{page['slide_id']}/texts").json()
        self.assertTrue(got["editable"], got)
        head = next(f for f in got["fields"] if f["ref"] == "k:headline")
        calls = len(writer_calls(self.log))
        r = self.fx.post(f"/api/decks/{deck}/pages/{page['slide_id']}/texts",
                         {"changes": [{"ref": head["ref"], "from": head["text"], "text": "人が書いた見出し"}]})
        self.assertEqual(r.status, 200, r.body)
        run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(len(writer_calls(self.log)), calls, "台本係は呼ばない")
        after_data, after_bytes = self.head(deck)
        self.assertEqual(after_data["slides"][3]["headline"], "人が書いた見出し")
        after_pieces = self.page_bytes(after_bytes, after_data)
        key = before_data["slides"][3]["id"]
        for k in before_pieces:
            if k != key:
                self.assertEqual(before_pieces[k], after_pieces[k], k)
        self.assertEqual(self.view(deck)["pages"][3]["title"], "人が書いた見出し")
        self.assert_screen_matches_pptx(deck)


    def test_a_text_the_engine_refuses_is_not_applied(self) -> None:
        """検査に落ちた書き換えは当てない（仕事は失敗で終わり、台本もページも元のまま。理由が残る）。"""
        from unittest import mock

        from quick_slide.worker import edit as worker_edit
        from quick_slide.worker.pipeline import CheckResult

        deck = self.make_deck()
        _, before_bytes = self.head(deck)
        page = self.view(deck)["pages"][3]
        got = self.fx.get(f"/api/decks/{deck}/pages/{page['slide_id']}/texts").json()
        head = next(f for f in got["fields"] if f["ref"] == "k:headline")
        refused = CheckResult(rc=2, problems=["NG: 見出しが長すぎる"], warnings=[], stopped=False)
        with mock.patch.object(worker_edit, "check_candidate", return_value=refused):
            r = self.fx.post(f"/api/decks/{deck}/pages/{page['slide_id']}/texts",
                             {"changes": [{"ref": head["ref"], "from": head["text"], "text": "検査に落ちる見出し"}]})
            self.assertEqual(r.status, 200, r.body)
            run = self.wait(deck)
        self.assertEqual(run["state"], "failed")
        self.assertIn("見出しが長すぎる", run["message"])
        self.assertEqual(self.head(deck)[1], before_bytes, "台本は元のまま")
        job = self.fx.get(f"/api/decks/{deck}/jobs/{r.json()['job_id']}").json()
        self.assertEqual((job["finished"], job["ok"]), (True, False))
        self.assertEqual(self.view(deck)["pages"][3]["title"], head["text"])

    def test_template_and_one_page_decks_are_refused(self) -> None:
        from quick_slide.server import edit_routes

        deck = self.make_deck()
        sid = self.view(deck)["pages"][0]["slide_id"]
        for args, word in (({"template_snapshot": {"snapshot_id": "x"}}, "デザインテンプレート"),
                           ({"mode": "pitch", "style": "infographic"}, "1 枚")):
            with self.fx.server.store_lock:
                real = self.fx.store.deck_run_args(deck)
            from unittest import mock
            with mock.patch.object(type(self.fx.store), "deck_run_args", return_value={**real, **args}):
                got = self.fx.get(f"/api/decks/{deck}/pages/{sid}/texts").json()
                self.assertEqual(got["editable"], False)
                self.assertIn(word, got["reason"])
                r = self.fx.post(f"/api/decks/{deck}/pages/{sid}/texts", {"changes": [{"ref": "k:title", "from": "a", "text": "b"}]})
                self.assertEqual((r.status, r.json()["error"]["code"]), (409, "text_not_editable"), r.body)
        self.assertTrue(edit_routes.TEMPLATE_TEXT_REASON)


class PitchTests(edit_helpers.EditBase):
    def test_pitch_pages_say_why_they_are_not_editable(self) -> None:
        deck = self.make_deck()
        page = self.view(deck)["pages"][0]
        got = self.fx.get(f"/api/decks/{deck}/pages/{page['slide_id']}/texts").json()
        self.assertEqual((got["editable"], got["fields"]), (False, []))
        self.assertIn("AI に頼む", got["reason"])
        r = self.fx.post(f"/api/decks/{deck}/pages/{page['slide_id']}/texts",
                         {"changes": [{"ref": "k:headline", "from": "a", "text": "b"}]})
        self.assertEqual((r.status, r.json()["error"]["code"]), (409, "text_not_editable"), r.body)


if __name__ == "__main__":
    unittest.main()
